"""Company modes: catalog products and domains, then an in-memory profile (1.2-4.2)."""

from pathlib import Path

import pytest

from leadforge.lead_ingestion.catalog import (
    Alias,
    Catalog,
    CatalogProduct,
    CatalogVendor,
    UnknownCatalogKeyError,
)
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.registry import SourceRegistry
from leadforge.lead_ingestion.target_profile import (
    TargetProfile,
    check_against_registry,
    effective_vocabulary,
)
from leadforge.outreach.company_plans import users_plan, workers_plan
from leadforge.outreach.config import load_outreach_config
from leadforge.outreach.errors import (
    MissingDomainError,
    NoProductSelectedError,
    UnknownTermError,
)
from leadforge.outreach.profile import catalog_base_profile, plan_to_profile
from leadforge.outreach.search_plan import SearchPlan, parse_request

CONFIG = Path(__file__).resolve().parents[4] / "config"
SOURCES = load_outreach_config(CONFIG / "outreach.yaml").sources


def _product(key: str, *aliases: str, uids: tuple[str, ...] = ()) -> CatalogProduct:
    return CatalogProduct(
        key=key,
        aliases=tuple(Alias(text=a) for a in aliases),
        technology_uids=uids,
    )


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    acme = CatalogVendor(
        key="acme",
        name="Acme Corp",
        domains=("acme.example",),
        partner_domains=(),
        products=(
            _product("widget", "Acme Widget", uids=("acme_widget",)),
            _product("gadget", "Acme Gadget"),
        ),
        ecosystem=(_product("sprocket", "Sprocket"),),
    )
    other = CatalogVendor(
        key="other",
        name="Other",
        domains=("other.example",),
        partner_domains=(),
        products=(_product("thing", "Other Thing", uids=("other_thing",)),),
        ecosystem=(),
    )
    return Catalog({"acme": acme, "other": other})


@pytest.fixture(scope="module")
def base(catalog: Catalog) -> TargetProfile:
    return catalog_base_profile(catalog, SOURCES)


# Verifies: specs/user-recognition/requirements.md#2.7
def test_the_base_profile_holds_every_catalog_product_and_ecosystem_entry(
    base: TargetProfile,
) -> None:
    assert set(base.terms()) == {"widget", "gadget", "sprocket", "thing"}
    assert base.vocabulary(SOURCES.domain_filter, "widget") == ("acme_widget",)
    assert base.vocabulary(SOURCES.phrase_search, "gadget") == ("Acme Gadget",)
    assert base.keyword_templates


# Verifies: specs/user-recognition/requirements.md#2.2
def test_a_users_search_names_catalog_products_and_maps_them_to_terms(
    catalog: Catalog, base: TargetProfile
) -> None:
    request = parse_request("users", "acme", products=["widget", "gadget"])
    plan = users_plan(request, catalog)
    profile = plan_to_profile(plan, base, SOURCES)

    assert plan.terms == ("widget", "gadget")
    assert plan.company == "Acme Corp"
    assert not plan.unmapped
    assert set(profile.terms()) == {"widget", "gadget"}
    assert profile.keyword_templates == base.keyword_templates


# Verifies: specs/user-recognition/requirements.md#2.2
def test_a_users_search_may_name_the_vendor_or_leave_it_to_the_product(
    catalog: Catalog,
) -> None:
    by_product = users_plan(parse_request("users", "q", products=["thing"]), catalog)
    by_vendor = users_plan(
        parse_request("users", "q", vendor="other", products=["thing"]), catalog
    )

    assert by_product.company == by_vendor.company == "Other"


# Verifies: specs/user-recognition/requirements.md#2.2
def test_a_users_search_with_no_product_is_rejected_before_any_provider_call(
    catalog: Catalog,
) -> None:
    with pytest.raises(NoProductSelectedError):
        users_plan(parse_request("users", "acme"), catalog)
    with pytest.raises(NoProductSelectedError):
        users_plan(parse_request("users", "acme", vendor="acme"), catalog)


# Verifies: specs/user-recognition/requirements.md#2.2
def test_a_users_search_naming_an_unknown_product_or_vendor_is_a_named_error(
    catalog: Catalog,
) -> None:
    with pytest.raises(UnknownCatalogKeyError):
        users_plan(parse_request("users", "q", products=["nope"]), catalog)
    with pytest.raises(UnknownCatalogKeyError):
        users_plan(
            parse_request("users", "q", vendor="acme", products=["thing"]), catalog
        )


# Verifies: outreach requirements 1.2
def test_the_plan_becomes_a_profile_object_with_no_file_written(
    catalog: Catalog, base: TargetProfile, tmp_path: Path, monkeypatch: object
) -> None:
    assert isinstance(monkeypatch, pytest.MonkeyPatch)
    monkeypatch.chdir(tmp_path)

    plan_to_profile(
        users_plan(parse_request("users", "acme", products=["widget"]), catalog),
        base,
        SOURCES,
    )
    plan_to_profile(
        workers_plan(parse_request("workers", "acme", ["acme.com"]), catalog),
        base,
        SOURCES,
    )

    assert list(tmp_path.iterdir()) == []


# Verifies: outreach requirements 3.1
def test_a_workers_plan_is_a_domain_filter_and_no_technology(
    catalog: Catalog, base: TargetProfile
) -> None:
    plan = workers_plan(parse_request("workers", "Nowhere Corp", ["x.com"]), catalog)
    profile = plan_to_profile(plan, base, SOURCES)

    assert plan.domains == ("x.com",)
    assert profile.terms() == ("nowhere_corp",)
    assert profile.vocabulary(SOURCES.domain_filter, "nowhere_corp") == {
        SOURCES.domain_key: ("x.com",)
    }
    assert profile.providers() == (SOURCES.domain_filter,)
    assert profile.keyword_templates == ()
    assert profile.competitors == {}


# Verifies: outreach requirements 3.1
def test_a_workers_profile_is_accepted_by_every_registered_source(
    catalog: Catalog,
) -> None:
    registry = SourceRegistry.discover()
    profile = plan_to_profile(
        workers_plan(
            parse_request("workers", "Acme", ["acme.com", "acme.io"]), catalog
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


# Verifies: specs/user-recognition/requirements.md#2.7
def test_a_workers_search_uses_the_vendor_domains_or_the_requested_ones(
    catalog: Catalog,
) -> None:
    configured = workers_plan(parse_request("workers", " ACME  corp "), catalog)
    by_key = workers_plan(parse_request("workers", "acme"), catalog)
    requested = workers_plan(
        parse_request("workers", "Acme Corp", ["acme.example.org"]), catalog
    )

    assert configured.domains == by_key.domains == ("acme.example",)
    assert requested.domains == ("acme.example.org",)


# Verifies: outreach requirements 3.3
def test_a_workers_search_with_no_known_domain_stops_naming_the_company(
    catalog: Catalog,
) -> None:
    with pytest.raises(MissingDomainError) as raised:
        workers_plan(parse_request("workers", "Nowhere Inc"), catalog)

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
