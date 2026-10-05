"""Lead, Company Signal, and Employment entities (task 2.1)."""

import copy
import pickle
import random
from typing import Any

import pytest
from pydantic import HttpUrl, ValidationError

from leadforge.lead_ingestion import models
from leadforge.lead_ingestion.models import (
    CanonicalLead,
    CompanySignal,
    ConflictingCompanySignalError,
    EmailStatus,
    Employment,
    IntentSignal,
    TechSignal,
    share_company_signals,
)

# Untyped constructors, for deliberately ill-typed or undeclared-field input.
lead_any: Any = CanonicalLead
company_any: Any = CompanySignal
employment_any: Any = Employment
tech_any: Any = TechSignal
intent_any: Any = IntentSignal


def company(company_id: str = "acme", **kw: Any) -> CompanySignal:
    kw.setdefault("name", "Acme Corp")
    kw.setdefault("domains", ("acme.com",))
    return CompanySignal(company_id=company_id, **kw)


def lead(**kw: Any) -> CanonicalLead:
    kw.setdefault("full_name", "Jane Doe")
    return CanonicalLead(**kw)


# ---------------------------------------------------------------- Lead basics


# Verifies: specs/lead-source-adapters/requirements.md#1.5
def test_lead_carries_all_identity_fields_both_flags_and_employments() -> None:
    fields = set(CanonicalLead.model_fields)
    assert {"email", "email_status", "linkedin_url", "full_name"} <= fields
    assert {"employments", "tech_signals", "intent_signals"} <= fields
    assert {"opt_out", "suppressed"} <= fields


# Verifies: specs/lead-source-adapters/requirements.md#1.5
def test_lead_constructs_with_full_identity_and_defaults() -> None:
    ld = CanonicalLead(
        email="jane@acme.com",
        email_status=EmailStatus.VERIFIED,
        linkedin_url=HttpUrl("https://www.linkedin.com/in/jane"),
        full_name="Jane Doe",
    )
    assert ld.email == "jane@acme.com"
    assert ld.email_status is EmailStatus.VERIFIED
    assert str(ld.linkedin_url) == "https://www.linkedin.com/in/jane"
    assert ld.full_name == "Jane Doe"
    assert ld.employments == ()
    assert ld.tech_signals == ()
    assert ld.intent_signals == ()
    assert ld.opt_out is False
    assert ld.suppressed is False


# Verifies: specs/lead-source-adapters/requirements.md#1.5
@pytest.mark.parametrize(
    "kw",
    [
        {"email": "a@b.co"},
        {"linkedin_url": "https://linkedin.com/in/x"},
        {"full_name": "Solo Name"},
    ],
    ids=["email-only", "linkedin-only", "name-only"],
)
def test_any_single_identity_attribute_is_enough_for_a_lead(kw: dict[str, Any]) -> None:
    assert CanonicalLead(**kw) is not None


# Verifies: specs/lead-source-adapters/requirements.md#1.5
def test_compliance_flags_are_independent_and_settable() -> None:
    assert lead(opt_out=True).suppressed is False
    assert lead(suppressed=True).opt_out is False
    both = lead(opt_out=True, suppressed=True)
    assert both.opt_out is True
    assert both.suppressed is True


# ------------------------------------------------- no person identity => no Lead


# Verifies: specs/lead-source-adapters/requirements.md#24.1
def test_record_with_no_person_identity_is_never_a_lead() -> None:
    with pytest.raises(ValidationError, match="person identity"):
        CanonicalLead()


# Verifies: specs/lead-source-adapters/requirements.md#24.1
def test_signals_and_employment_do_not_make_a_lead_without_a_person() -> None:
    emp = Employment(company=company(), title="CTO", is_current=True)
    with pytest.raises(ValidationError, match="person identity"):
        CanonicalLead(
            employments=(emp,),
            tech_signals=(TechSignal(label="cassandra", strength=0.9),),
            opt_out=True,
        )


# Verifies: specs/lead-source-adapters/requirements.md#24.1
@pytest.mark.parametrize("blank", ["", " ", "\t\n"])
def test_blank_identity_strings_count_as_no_identity(blank: str) -> None:
    for field in ("email", "full_name"):
        with pytest.raises(ValidationError):
            lead_any(**{field: blank})


# Verifies: specs/lead-source-adapters/requirements.md#24.1
def test_company_only_record_is_a_company_signal_not_a_lead() -> None:
    cs = CompanySignal(company_id="acme", domains=("acme.com",))
    assert not isinstance(cs, CanonicalLead)
    assert not hasattr(cs, "email")
    assert not hasattr(cs, "full_name")
    assert not hasattr(cs, "linkedin_url")
    with pytest.raises(ValidationError):
        CanonicalLead(**cs.model_dump())


# Verifies: specs/lead-source-adapters/requirements.md#24.1
def test_a_company_cannot_be_smuggled_in_as_a_lead_via_name() -> None:
    # A bare company name in full_name is still a name; nothing marks it as a
    # company, but a CompanySignal can never be passed where a Lead is typed.
    assert CompanySignal.model_fields.keys().isdisjoint(
        {"email", "email_status", "linkedin_url", "full_name", "opt_out", "suppressed"}
    )


# ------------------------------------------------------- strictness / immutable


# Verifies: specs/lead-source-adapters/requirements.md#1.4
@pytest.mark.parametrize(
    "build",
    [
        lambda: lead_any(full_name="J", surprise=1),
        lambda: company_any(company_id="a", name="A", surprise=1),
        lambda: employment_any(company=company(), surprise=1),
        lambda: tech_any(label="x", strength=0.1, surprise=1),
        lambda: intent_any(label="x", strength=0.1, surprise=1),
    ],
    ids=["lead", "company", "employment", "tech", "intent"],
)
def test_undeclared_fields_are_rejected_at_construction(build: Any) -> None:
    with pytest.raises(ValidationError, match="surprise"):
        build()


# Verifies: specs/lead-source-adapters/requirements.md#1.4
@pytest.mark.parametrize(
    ("obj", "attr", "value"),
    [
        (lead(), "full_name", "Other"),
        (lead(), "suppressed", True),
        (company(), "name", "Other"),
        (Employment(company=company()), "title", "CEO"),
        (TechSignal(label="x", strength=0.5), "strength", 0.1),
    ],
    ids=["lead-name", "lead-flag", "company", "employment", "signal"],
)
def test_every_entity_is_frozen(obj: Any, attr: str, value: Any) -> None:
    with pytest.raises(ValidationError, match="frozen"):
        setattr(obj, attr, value)


# Verifies: specs/lead-source-adapters/requirements.md#1.4
def test_collections_are_immutable_tuples_and_models_are_hashable() -> None:
    ld = lead(employments=[Employment(company=company())])  # list input coerced
    assert isinstance(ld.employments, tuple)
    assert isinstance(company(domains=["a.com", "b.com"]).domains, tuple)
    assert hash(ld) == hash(copy.deepcopy(ld))
    assert len({ld, copy.deepcopy(ld)}) == 1


# Verifies: specs/lead-source-adapters/requirements.md#1.4
def test_entities_survive_pickle_and_dump_roundtrip() -> None:
    ld = lead(
        email="j@acme.com",
        email_status=EmailStatus.ACCEPT_ALL,
        employments=(Employment(company=company(), title="VP", is_current=True),),
        tech_signals=(TechSignal(label="cassandra", strength=0.4),),
    )
    assert pickle.loads(pickle.dumps(ld)) == ld
    assert CanonicalLead.model_validate(ld.model_dump()) == ld


# Verifies: specs/lead-source-adapters/requirements.md#1.1
def test_lead_is_the_only_lead_type_in_the_module() -> None:
    lead_like = [
        n for n, o in vars(models).items() if isinstance(o, type) and n.endswith("Lead")
    ]
    assert lead_like == ["CanonicalLead"]


# ---------------------------------------------------------- email + verification


# Verifies: specs/lead-source-adapters/requirements.md#1.5
def test_email_status_vocabulary_matches_design() -> None:
    assert {s.value for s in EmailStatus} == {
        "verified",
        "accept_all",
        "unverified",
        "invalid",
        "unknown",
    }


# Verifies: specs/lead-source-adapters/requirements.md#1.5
@pytest.mark.parametrize("status", list(EmailStatus))
def test_every_email_status_is_accepted_with_an_email(status: EmailStatus) -> None:
    assert lead(email="a@b.co", email_status=status).email_status is status


# Verifies: specs/lead-source-adapters/requirements.md#1.5
def test_email_status_defaults_to_unknown_never_verified() -> None:
    assert lead(email="a@b.co").email_status is EmailStatus.UNKNOWN
    assert lead().email_status is EmailStatus.UNKNOWN


# Verifies: specs/lead-source-adapters/requirements.md#1.5
@pytest.mark.parametrize(
    "status", [s for s in EmailStatus if s is not EmailStatus.UNKNOWN]
)
def test_email_status_without_an_email_is_rejected(status: EmailStatus) -> None:
    with pytest.raises(ValidationError, match="email_status"):
        lead(email=None, email_status=status)


# Verifies: specs/lead-source-adapters/requirements.md#1.5
def test_email_status_string_values_are_accepted_and_junk_rejected() -> None:
    assert lead(email="a@b.co", email_status="verified").email_status is (
        EmailStatus.VERIFIED
    )
    with pytest.raises(ValidationError):
        lead(email="a@b.co", email_status="deliverable")


# Verifies: specs/lead-source-adapters/requirements.md#1.5
@pytest.mark.parametrize(
    "bad",
    ["nope", "a@", "@b.co", "a@b", "a b@c.co", "a@@b.co", "a@b .co", "a@b.co\n"],
)
def test_malformed_email_is_rejected_not_coerced(bad: str) -> None:
    with pytest.raises(ValidationError, match="email"):
        lead(email=bad)


# Verifies: specs/lead-source-adapters/requirements.md#1.5
def test_email_is_stored_verbatim() -> None:
    assert lead(email="Jane.Doe+x@Acme.COM").email == "Jane.Doe+x@Acme.COM"


# Verifies: specs/lead-source-adapters/requirements.md#1.5
@pytest.mark.parametrize("bad", ["not a url", "ftp://linkedin.com/in/x", "linkedin"])
def test_malformed_linkedin_url_is_rejected(bad: str) -> None:
    with pytest.raises(ValidationError, match="linkedin_url"):
        lead(linkedin_url=bad)


# ------------------------------------------------------------------- employment


# Verifies: specs/lead-source-adapters/requirements.md#1.7
def test_lead_has_no_employer_domain_attribute_of_any_kind() -> None:
    names = set(CanonicalLead.model_fields)
    assert "company_domain" not in names
    assert "lead_scope" not in names
    assert not any("domain" in n or "company" in n or "employer" in n for n in names)
    assert not hasattr(lead(), "company_domain")
    with pytest.raises(ValidationError, match="company_domain"):
        lead(company_domain="acme.com")


# Verifies: specs/lead-source-adapters/requirements.md#1.7
def test_employer_is_reachable_only_through_employment() -> None:
    acme = company()
    ld = lead(employments=(Employment(company=acme, title="CTO", is_current=True),))
    assert ld.employments[0].company is acme
    assert ld.employments[0].company.domains == ("acme.com",)


# Verifies: specs/lead-source-adapters/requirements.md#24.2
def test_employment_requires_a_company_signal() -> None:
    with pytest.raises(ValidationError, match="company"):
        Employment()  # type: ignore[call-arg]
    with pytest.raises(ValidationError, match="company"):
        Employment(company=None)  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="company"):
        Employment(company="acme.com")  # type: ignore[arg-type]


# Verifies: specs/lead-source-adapters/requirements.md#24.2
def test_employment_title_and_currency_are_optional_provider_facts() -> None:
    e = Employment(company=company())
    assert e.title is None
    assert e.is_current is None  # unknown is not False
    assert Employment(company=company(), is_current=False).is_current is False


# Verifies: specs/lead-source-adapters/requirements.md#24.2
def test_blank_title_is_rejected() -> None:
    with pytest.raises(ValidationError, match="title"):
        Employment(company=company(), title="  ")


# Verifies: specs/lead-source-adapters/requirements.md#24.2
def test_lead_holds_multiple_employments_current_and_past() -> None:
    past1 = Employment(company=company("old1"), title="Eng", is_current=False)
    past2 = Employment(company=company("old2"), title="Lead", is_current=False)
    now = Employment(company=company("new"), title="CTO", is_current=True)
    ld = lead(employments=(past1, now, past2))
    assert ld.employments == (past1, now, past2)  # order preserved
    assert [e.company.company_id for e in ld.employments if e.is_current] == ["new"]
    assert [e.company.company_id for e in ld.employments if e.is_current is False] == [
        "old1",
        "old2",
    ]


# Verifies: specs/lead-source-adapters/requirements.md#24.2
def test_only_historical_employments_is_valid() -> None:
    ld = lead(employments=(Employment(company=company(), is_current=False),))
    assert not any(e.is_current for e in ld.employments)


# Verifies: specs/lead-source-adapters/requirements.md#24.2
def test_two_current_employments_are_rejected() -> None:
    with pytest.raises(ValidationError, match="current"):
        lead(
            employments=(
                Employment(company=company("a"), is_current=True),
                Employment(company=company("b"), is_current=True),
            )
        )


# Verifies: specs/lead-source-adapters/requirements.md#24.2
def test_unknown_currency_never_counts_toward_the_one_current_limit() -> None:
    ld = lead(
        employments=(
            Employment(company=company("a"), is_current=True),
            Employment(company=company("b")),
            Employment(company=company("c")),
        )
    )
    assert len(ld.employments) == 3


# Verifies: specs/lead-source-adapters/requirements.md#24.2
def test_identical_employment_listed_twice_is_rejected() -> None:
    e = Employment(company=company(), title="CTO", is_current=False)
    with pytest.raises(ValidationError, match="duplicate"):
        lead(employments=(e, e))


# Verifies: specs/lead-source-adapters/requirements.md#24.2
def test_same_company_with_different_stints_is_allowed() -> None:
    acme = company()
    ld = lead(
        employments=(
            Employment(company=acme, title="Eng", is_current=False),
            Employment(company=acme, title="VP", is_current=True),
        )
    )
    assert len(ld.employments) == 2


# Verifies: specs/lead-source-adapters/requirements.md#24.2
def test_two_company_signals_with_one_id_inside_a_lead_must_agree() -> None:
    with pytest.raises(ValidationError, match="company_id"):
        lead(
            employments=(
                Employment(company=company("acme", name="Acme"), is_current=False),
                Employment(company=company("acme", name="Other"), is_current=True),
            )
        )


# ----------------------------------------------------------------- company signal


# Verifies: specs/lead-source-adapters/requirements.md#24.1
def test_company_signal_requires_id_and_some_organization_identity() -> None:
    with pytest.raises(ValidationError):
        CompanySignal()  # type: ignore[call-arg]
    with pytest.raises(ValidationError, match="organization"):
        CompanySignal(company_id="x")
    with pytest.raises(ValidationError, match="company_id"):
        CompanySignal(company_id=" ", name="A")
    assert CompanySignal(company_id="x", name="A").domains == ()
    assert CompanySignal(company_id="x", domains=("a.com",)).name is None


# Verifies: specs/lead-source-adapters/requirements.md#24.1
def test_company_signal_owns_several_domains() -> None:
    cs = company(domains=("acme.com", "acme.io"))
    assert cs.domains == ("acme.com", "acme.io")


# Verifies: specs/lead-source-adapters/requirements.md#24.1
@pytest.mark.parametrize("bad", [("",), (" ",), ("a.com", "a.com"), ("A b.com",)])
def test_company_signal_rejects_blank_duplicate_or_spaced_domains(
    bad: tuple[str, ...],
) -> None:
    with pytest.raises(ValidationError, match="domain"):
        company(domains=bad)


# Verifies: specs/lead-source-adapters/requirements.md#24.4
def test_company_signal_carries_signals_with_their_own_strength() -> None:
    cs = company(
        tech_signals=(TechSignal(label="cassandra", strength=0.8),),
        intent_signals=(IntentSignal(label="hiring", strength=0.2),),
    )
    assert cs.tech_signals[0].strength == 0.8
    assert cs.intent_signals[0].strength == 0.2


# ---------------------------------------------------------------------- signals


# Verifies: specs/lead-source-adapters/requirements.md#24.4
def test_lead_carries_technographic_and_intent_signals_with_strength() -> None:
    ld = lead(
        tech_signals=(TechSignal(label="cassandra", strength=1.0),),
        intent_signals=(IntentSignal(label="pricing-page", strength=0.0),),
    )
    assert ld.tech_signals[0].label == "cassandra"
    assert ld.intent_signals[0].strength == 0.0


# Verifies: specs/lead-source-adapters/requirements.md#24.4
@pytest.mark.parametrize(
    "bad", [-0.01, 1.01, float("nan"), float("inf"), float("-inf")]
)
def test_signal_strength_must_be_a_finite_unit_interval_value(bad: float) -> None:
    with pytest.raises(ValidationError, match="strength"):
        TechSignal(label="x", strength=bad)


# Verifies: specs/lead-source-adapters/requirements.md#24.4
def test_signal_requires_label_and_strength_and_rejects_blank_label() -> None:
    with pytest.raises(ValidationError):
        TechSignal(label="x")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        TechSignal(strength=0.5)  # type: ignore[call-arg]
    with pytest.raises(ValidationError, match="label"):
        IntentSignal(label=" ", strength=0.5)


# Verifies: specs/lead-source-adapters/requirements.md#24.4
def test_tech_and_intent_signals_are_not_interchangeable() -> None:
    with pytest.raises(ValidationError):
        lead(tech_signals=(IntentSignal(label="x", strength=0.5),))
    with pytest.raises(ValidationError):
        lead(intent_signals=(TechSignal(label="x", strength=0.5),))


# Verifies: specs/lead-source-adapters/requirements.md#24.4
def test_signal_strength_does_not_participate_in_lead_identity_fields() -> None:
    # Strength is plain evidence data: no identity field is derived from it.
    a = lead(tech_signals=(TechSignal(label="x", strength=0.1),))
    b = lead(tech_signals=(TechSignal(label="x", strength=0.9),))
    for f in ("email", "linkedin_url", "full_name"):
        assert getattr(a, f) == getattr(b, f)


# ---------------------------------------------------------- shared Company Signal


# Verifies: specs/lead-source-adapters/requirements.md#24.3
def test_one_company_signal_instance_is_shared_by_many_leads() -> None:
    acme = company()
    leads = [
        lead(
            full_name=f"Person {i}",
            employments=(Employment(company=acme, is_current=True),),
        )
        for i in range(25)
    ]
    assert {id(ld.employments[0].company) for ld in leads} == {id(acme)}
    assert all(ld.employments[0].company is acme for ld in leads)


# Verifies: specs/lead-source-adapters/requirements.md#24.3
def test_sharing_a_company_signal_does_not_merge_the_leads() -> None:
    acme = company()
    a = lead(full_name="A", employments=(Employment(company=acme),))
    b = lead(full_name="B", employments=(Employment(company=acme),))
    assert a != b


# Verifies: specs/lead-source-adapters/requirements.md#24.3
def test_constructing_a_lead_does_not_copy_its_company_signal() -> None:
    acme = company()
    emp = Employment(company=acme)
    assert emp.company is acme
    assert lead(employments=(emp,)).employments[0] is emp


# Verifies: specs/lead-source-adapters/requirements.md#24.3
def test_share_company_signals_collapses_equal_copies_to_one_instance() -> None:
    copies = [company() for _ in range(3)]
    assert copies[0] is not copies[1]
    leads = [
        lead(full_name=f"P{i}", employments=(Employment(company=c, is_current=True),))
        for i, c in enumerate(copies)
    ]
    shared = share_company_signals(leads)
    assert len(shared) == 3
    assert len({id(ld.employments[0].company) for ld in shared}) == 1
    # everything except the company instance is untouched, order preserved
    assert [ld.full_name for ld in shared] == ["P0", "P1", "P2"]
    assert shared == tuple(leads)  # equal by value


# Verifies: specs/lead-source-adapters/requirements.md#24.3
def test_share_company_signals_keeps_distinct_companies_distinct() -> None:
    a, b = company("a", name="A"), company("b", name="B")
    leads = [
        lead(full_name="X", employments=(Employment(company=a, is_current=False),)),
        lead(full_name="Y", employments=(Employment(company=b, is_current=True),)),
        lead(full_name="Z", employments=(Employment(company=company("a", name="A")),)),
    ]
    out = share_company_signals(leads)
    assert out[0].employments[0].company is out[2].employments[0].company
    assert out[0].employments[0].company is not out[1].employments[0].company


# Verifies: specs/lead-source-adapters/requirements.md#24.3
def test_share_company_signals_preserves_employment_fields() -> None:
    ld = lead(
        employments=(
            Employment(company=company("a"), title="Eng", is_current=False),
            Employment(company=company("b"), title="CTO", is_current=True),
        )
    )
    (out,) = share_company_signals([ld])
    assert [(e.title, e.is_current) for e in out.employments] == [
        ("Eng", False),
        ("CTO", True),
    ]


# Verifies: specs/lead-source-adapters/requirements.md#24.3
def test_share_company_signals_rejects_conflicting_content_for_one_id() -> None:
    leads = [
        lead(full_name="A", employments=(Employment(company=company(name="Acme")),)),
        lead(full_name="B", employments=(Employment(company=company(name="Acme2")),)),
    ]
    with pytest.raises(ConflictingCompanySignalError, match="acme"):
        share_company_signals(leads)


# Verifies: specs/lead-source-adapters/requirements.md#24.3
def test_share_company_signals_handles_empty_and_employmentless_leads() -> None:
    assert share_company_signals([]) == ()
    solo = lead()
    assert share_company_signals([solo]) == (solo,)


# Verifies: specs/lead-source-adapters/requirements.md#24.3
def test_share_company_signals_is_idempotent_and_does_not_mutate_input() -> None:
    leads = [
        lead(full_name=f"P{i}", employments=(Employment(company=company()),))
        for i in range(3)
    ]
    before = [ld.employments[0].company for ld in leads]
    once = share_company_signals(leads)
    assert [ld.employments[0].company for ld in leads] == before
    assert all(
        ld.employments[0].company is c for ld, c in zip(leads, before, strict=True)
    )
    assert share_company_signals(once) == once


# Verifies: specs/lead-source-adapters/requirements.md#24.3
def test_conflicting_error_is_picklable_and_named() -> None:
    err = ConflictingCompanySignalError("acme")
    assert "acme" in str(err)
    assert pickle.loads(pickle.dumps(err)).args == err.args
    assert copy.copy(err).args == err.args


# Verifies: specs/lead-source-adapters/requirements.md#24.3 (property)
@pytest.mark.parametrize("seed", range(20))
def test_property_sharing_yields_one_instance_per_company_id(seed: int) -> None:
    rng = random.Random(seed)
    ids = [f"c{i}" for i in range(rng.randint(1, 5))]
    leads = []
    for n in range(rng.randint(0, 15)):
        chosen = rng.sample(ids, rng.randint(0, len(ids)))
        emps = tuple(
            Employment(
                company=company(cid, name=f"N-{cid}", domains=(f"{cid}.com",)),
                is_current=(i == 0 and rng.random() < 0.5),
            )
            for i, cid in enumerate(chosen)
        )
        leads.append(lead(full_name=f"P{n}", employments=emps))
    out = share_company_signals(leads)
    assert out == tuple(leads)
    by_id: dict[str, set[int]] = {}
    for ld in out:
        for e in ld.employments:
            by_id.setdefault(e.company.company_id, set()).add(id(e.company))
    assert all(len(v) == 1 for v in by_id.values())
    assert share_company_signals(out) == out


# Verifies: specs/lead-source-adapters/requirements.md#1.5 (property)
@pytest.mark.parametrize("seed", range(20))
def test_property_a_lead_exists_iff_some_person_identity_is_present(seed: int) -> None:
    rng = random.Random(seed)
    email = rng.choice([None, "a@b.co"])
    url = rng.choice([None, "https://linkedin.com/in/q"])
    name = rng.choice([None, "N"])
    kw = {
        k: v
        for k, v in (("email", email), ("linkedin_url", url), ("full_name", name))
        if v
    }
    if kw:
        assert lead_any(**kw).model_dump(exclude_none=True)
    else:
        with pytest.raises(ValidationError):
            lead_any(**kw)


# Verifies: specs/lead-source-adapters/requirements.md#1.4 (property)
@pytest.mark.parametrize("seed", range(20))
def test_property_dump_validate_roundtrip_is_identity(seed: int) -> None:
    rng = random.Random(seed)
    emps = tuple(
        Employment(
            company=company(f"c{i}", domains=(f"c{i}.com", f"c{i}.io")),
            title=rng.choice([None, "Eng"]),
            is_current=False,
        )
        for i in range(rng.randint(0, 4))
    )
    ld = lead(
        email=rng.choice([None, "a@b.co"]),
        employments=emps,
        tech_signals=tuple(
            TechSignal(label=f"t{i}", strength=rng.random())
            for i in range(rng.randint(0, 3))
        ),
        opt_out=rng.random() < 0.5,
    )
    assert CanonicalLead.model_validate(ld.model_dump()) == ld
    assert CanonicalLead.model_validate_json(ld.model_dump_json()) == ld
