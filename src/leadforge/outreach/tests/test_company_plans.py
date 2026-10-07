"""Company modes: terms and domains from config, then an in-memory profile (1.2-4.2)."""

from pathlib import Path

import pytest
import yaml

from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.registry import SourceRegistry
from leadforge.lead_ingestion.target_profile import (
    TargetProfile,
    check_against_registry,
    effective_vocabulary,
    load_target_profile,
)
from leadforge.outreach.company_plans import users_plan, workers_plan
from leadforge.outreach.company_terms import (
    CompanyTerms,
    load_company_terms,
    normalize_company,
)
from leadforge.outreach.config import load_outreach_config
from leadforge.outreach.errors import (
    MissingDomainError,
    OutreachConfigError,
    UnknownTermError,
)
from leadforge.outreach.profile import plan_to_profile
from leadforge.outreach.search_plan import SearchPlan, parse_request

CONFIG = Path(__file__).resolve().parents[4] / "config"
SOURCES = load_outreach_config(CONFIG / "outreach.yaml").sources


@pytest.fixture(scope="module")
def companies() -> CompanyTerms:
    return load_company_terms(CONFIG / "company_terms.yaml")


@pytest.fixture(scope="module")
def base() -> TargetProfile:
    return load_target_profile(CONFIG / "target_profile.yaml")


# Verifies: outreach requirements 4.1
def test_every_configured_term_is_in_the_base_profile(
    companies: CompanyTerms, base: TargetProfile
) -> None:
    for name, entry in companies.companies.items():
        assert set(entry.terms) <= set(base.terms()), name


# Verifies: outreach requirements 4.1
def test_a_users_search_for_a_company_maps_to_its_terms_in_the_profile(
    companies: CompanyTerms, base: TargetProfile
) -> None:
    name, entry = next(
        (n, e) for n, e in companies.companies.items() if len(e.terms) > 1
    )
    plan = users_plan(parse_request("users", f" {name.upper()} "), companies)
    profile = plan_to_profile(plan, base, SOURCES)

    assert plan.terms == entry.terms
    assert not plan.unmapped
    assert set(profile.terms()) == set(entry.terms)
    for term in entry.terms:
        assert profile.vocabulary(SOURCES.phrase_search, term) is not None
        assert {**profile.technologies, **profile.competitors}[term] == {
            **base.technologies,
            **base.competitors,
        }[term]


# Verifies: outreach requirements 4.1
def test_a_company_with_no_entry_is_searched_by_name_and_flagged(
    companies: CompanyTerms, base: TargetProfile
) -> None:
    plan = users_plan(parse_request("users", "Unlisted Corp"), companies)
    profile = plan_to_profile(plan, base, SOURCES)

    assert plan.unmapped
    assert plan.terms == ()
    assert profile.terms() == ("unlisted_corp",)
    assert profile.vocabulary(SOURCES.phrase_search, "unlisted_corp") == (
        "Unlisted Corp",
    )


# Verifies: outreach requirements 4.2
def test_users_mode_keeps_the_keyword_templates_that_gather_company_signals(
    companies: CompanyTerms, base: TargetProfile
) -> None:
    mapped = plan_to_profile(
        users_plan(parse_request("users", "mongodb"), companies), base, SOURCES
    )
    unmapped = plan_to_profile(
        users_plan(parse_request("users", "nobody"), companies), base, SOURCES
    )

    assert base.keyword_templates
    assert mapped.keyword_templates == base.keyword_templates
    assert unmapped.keyword_templates == base.keyword_templates


# Verifies: outreach requirements 1.2
def test_the_plan_becomes_a_profile_object_with_no_file_written(
    companies: CompanyTerms, base: TargetProfile, tmp_path: Path, monkeypatch: object
) -> None:
    assert isinstance(monkeypatch, pytest.MonkeyPatch)
    monkeypatch.chdir(tmp_path)

    plan_to_profile(
        users_plan(parse_request("users", "mongodb"), companies), base, SOURCES
    )
    plan_to_profile(
        workers_plan(parse_request("workers", "acme", ["acme.com"]), companies),
        base,
        SOURCES,
    )

    assert list(tmp_path.iterdir()) == []


# Verifies: outreach requirements 3.1
def test_a_workers_plan_is_a_domain_filter_and_no_technology(
    companies: CompanyTerms, base: TargetProfile
) -> None:
    plan = workers_plan(parse_request("workers", "Acme Corp", ["acme.com"]), companies)
    profile = plan_to_profile(plan, base, SOURCES)

    assert plan.domains == ("acme.com",)
    assert profile.terms() == ("acme_corp",)
    assert profile.vocabulary(SOURCES.domain_filter, "acme_corp") == {
        SOURCES.domain_key: ("acme.com",)
    }
    assert profile.providers() == (SOURCES.domain_filter,)
    assert profile.keyword_templates == ()
    assert profile.competitors == {}


# Verifies: outreach requirements 3.1
def test_a_workers_profile_is_accepted_by_every_registered_source() -> None:
    registry = SourceRegistry.discover()
    profile = plan_to_profile(
        workers_plan(
            parse_request("workers", "Acme", ["acme.com", "acme.io"]),
            CompanyTerms(companies={}),
        ),
        TargetProfile(),
        SOURCES,
    )

    assert check_against_registry(profile, registry, path="in-memory") == ()
    source_class = registry.source_class(SOURCES.domain_filter)
    vocabulary = effective_vocabulary(profile, source_class)
    source_class.from_run(
        DataMode.SYNTHETIC,
        transport=source_class.build_transport(DataMode.SYNTHETIC),
        pacing=None,
        vocabulary=vocabulary,
    )


# Verifies: outreach requirements 3.3
def test_a_workers_search_uses_the_configured_domains_or_the_requested_ones(
    companies: CompanyTerms,
) -> None:
    configured = workers_plan(parse_request("workers", "MongoDB"), companies)
    requested = workers_plan(
        parse_request("workers", "MongoDB", ["mongo.example.org"]), companies
    )

    assert configured.domains == ("mongodb.com",)
    assert requested.domains == ("mongo.example.org",)


# Verifies: outreach requirements 3.3
def test_a_workers_search_with_no_known_domain_stops_naming_the_company(
    companies: CompanyTerms,
) -> None:
    with pytest.raises(MissingDomainError) as raised:
        workers_plan(parse_request("workers", "Nowhere Inc"), companies)

    assert raised.value.company == "Nowhere Inc"
    assert "--domain" in str(raised.value)


# Verifies: outreach requirements 1.3
def test_a_plan_naming_an_unknown_term_never_reaches_a_profile(
    base: TargetProfile,
) -> None:
    plan = SearchPlan(
        mode="free_text", query="q", terms=("not_a_term",), compiler="offline"
    )

    with pytest.raises(UnknownTermError):
        plan_to_profile(plan, base, SOURCES)


# Verifies: outreach requirements 4.1
def test_company_lookup_ignores_case_and_spacing(companies: CompanyTerms) -> None:
    assert normalize_company("  Data   Stax ") == "data stax"
    name = next(iter(companies.companies))
    assert companies.entry(name.upper()) == companies.entry(f" {name} ")
    assert companies.entry("nobody") is None


def _write(tmp_path: Path, document: object) -> Path:
    path = tmp_path / "company_terms.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return path


# Verifies: outreach requirements 4.1
@pytest.mark.parametrize(
    "document",
    [
        {"companies": {"a": {"terms": "not-a-list"}}},
        {"companies": {"a": {"surprise": 1}}},
        {"companies": {"Acme": {}, " acme": {}}},
        {"other": {}},
    ],
)
def test_a_bad_company_terms_file_is_a_named_error(
    tmp_path: Path, document: object
) -> None:
    with pytest.raises(OutreachConfigError):
        load_company_terms(_write(tmp_path, document))
