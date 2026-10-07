"""Search Plan: three modes, unknown mode and term are named errors (1.1, 1.3)."""

import pytest
from pydantic import ValidationError

from leadforge.outreach.errors import UnknownModeError, UnknownTermError
from leadforge.outreach.search_plan import (
    MODES,
    SearchPlan,
    check_terms,
    parse_mode,
    parse_request,
)


def _plan(**over: object) -> SearchPlan:
    fields: dict[str, object] = {
        "mode": "free_text",
        "query": "q",
        "compiler": "offline",
        **over,
    }
    return SearchPlan.model_validate(fields)


# Verifies: outreach requirements 1.1
def test_the_three_modes_are_accepted() -> None:
    assert MODES == ("free_text", "workers", "users")
    assert [parse_mode(m) for m in MODES] == list(MODES)


# Verifies: outreach requirements 1.1
@pytest.mark.parametrize("mode", ["", "Workers", "company", "free text", "users "])
def test_any_other_mode_is_a_named_error(mode: str) -> None:
    with pytest.raises(UnknownModeError) as raised:
        parse_request(mode, "acme")

    assert raised.value.mode == mode


# Verifies: outreach requirements 1.1
def test_a_request_is_trimmed_and_its_domains_are_lower_cased() -> None:
    request = parse_request("workers", "  Acme  ", [" ACME.com "])

    assert (request.query, request.domains) == ("Acme", ("acme.com",))


# Verifies: outreach requirements 1.1
@pytest.mark.parametrize("domain", ["not a domain", "acme", "-a.com", "a..com"])
def test_a_malformed_domain_is_refused(domain: str) -> None:
    with pytest.raises(ValidationError):
        parse_request("workers", "acme", [domain])


# Verifies: outreach requirements 1.1
def test_a_plan_takes_no_extra_field_and_is_frozen() -> None:
    with pytest.raises(ValidationError):
        _plan(vendor="x")
    plan = _plan()
    with pytest.raises(ValidationError):
        plan.query = "other"


# Verifies: outreach requirements 1.3
def test_an_unknown_term_is_a_named_error_naming_it() -> None:
    plan = _plan(terms=("term_a", "madeup"))

    with pytest.raises(UnknownTermError) as raised:
        check_terms(plan, {"term_a", "term_b"})

    assert raised.value.term == "madeup"


# Verifies: outreach requirements 1.3
def test_known_terms_and_an_unmapped_plan_with_no_terms_pass() -> None:
    check_terms(_plan(terms=("term_a",)), {"term_a"})
    check_terms(_plan(mode="users", unmapped=True), set())
