"""Match Key extraction by durability (task 16.1, Requirements 8.1-8.3, 8.11)."""

import itertools
from datetime import UTC, datetime
from typing import Any

import pytest

from leadforge.lead_ingestion.base_source import LeadContribution
from leadforge.lead_ingestion.match_keys import (
    MatchKeyKind,
    corroborates,
    extract_match_keys,
)
from leadforge.lead_ingestion.models import (
    ConfidenceOrigin,
    DataMode,
    EmailStatus,
    FieldProvenance,
    UntrustedText,
)

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def untrusted(text: str) -> UntrustedText:
    return UntrustedText(value=text, truncated=False, original_length=len(text))


def contribution(source: str = "s", **values: Any) -> LeadContribution:
    """Build a contribution; keys use ``__`` for ``.`` (person__email)."""
    mapped = {k.replace("__", "."): v for k, v in values.items()}
    provenance = tuple(
        FieldProvenance(
            canonical_path=path,
            source_name=source,
            data_mode=DataMode.SYNTHETIC,
            fetched_at=NOW,
            raw_field_path="raw",
            confidence_origin=ConfidenceOrigin.NONE,
            untrusted=isinstance(value, UntrustedText),
        )
        for path, value in mapped.items()
    )
    return LeadContribution(source_name=source, values=mapped, provenance=provenance)


def keys_of(**values: Any) -> list[tuple[MatchKeyKind, str]]:
    return [(k.kind, k.value) for k in extract_match_keys(contribution(**values)).keys]


def one_key(kind: MatchKeyKind, **values: Any) -> str:
    found = [v for k, v in keys_of(**values) if k is kind]
    assert len(found) == 1
    return found[0]


# Verifies: specs/lead-source-adapters/requirements.md#8.1
@pytest.mark.parametrize(
    "url",
    [
        "https://www.linkedin.com/in/jane-doe",
        "https://WWW.LinkedIn.com/in/Jane-Doe/",
        "https://www.linkedin.com/in/jane-doe?trk=abc&x=1",
        "https://www.linkedin.com/in/jane-doe/#section",
        "  https://www.linkedin.com/in/JANE-DOE/?a=b#c  ",
        "http://www.linkedin.com/in/jane-doe",
    ],
)
def test_linkedin_urls_differing_only_by_case_query_fragment_slash_share_one_key(
    url: str,
) -> None:
    value = one_key(MatchKeyKind.LINKEDIN_URL, person__linkedin_url=url)
    assert value == "www.linkedin.com/in/jane-doe"


# Verifies: specs/lead-source-adapters/requirements.md#8.1
def test_linkedin_key_survives_a_change_of_employer() -> None:
    before = keys_of(
        person__linkedin_url="https://linkedin.com/in/jd", company__domain="a.com"
    )
    after = keys_of(
        person__linkedin_url="https://linkedin.com/in/jd", company__domain="b.com"
    )
    assert set(before) & set(after) == {
        (MatchKeyKind.LINKEDIN_URL, "linkedin.com/in/jd")
    }


# Verifies: specs/lead-source-adapters/requirements.md#8.1
@pytest.mark.parametrize(
    "url", ["", "   ", "https://www.linkedin.com", "https://www.linkedin.com/", "/"]
)
def test_a_linkedin_url_naming_no_profile_yields_no_key(url: str) -> None:
    assert keys_of(person__linkedin_url=url) == []


# Verifies: specs/lead-source-adapters/requirements.md#8.2
def test_case_differing_verified_emails_give_one_equal_key() -> None:
    a = one_key(
        MatchKeyKind.VERIFIED_EMAIL,
        person__email=" Jane.Doe@Example.COM ",
        person__email_status=EmailStatus.VERIFIED,
    )
    b = one_key(
        MatchKeyKind.VERIFIED_EMAIL,
        person__email="jane.doe@example.com",
        person__email_status=EmailStatus.VERIFIED,
    )
    assert a == b == "jane.doe@example.com"


# Verifies: specs/lead-source-adapters/requirements.md#8.2
def test_a_plus_address_is_kept_as_written() -> None:
    value = one_key(
        MatchKeyKind.VERIFIED_EMAIL,
        person__email="Jane+x@example.com",
        person__email_status=EmailStatus.VERIFIED,
    )
    assert value == "jane+x@example.com"


# Verifies: specs/lead-source-adapters/requirements.md#8.11
@pytest.mark.parametrize(
    "status",
    [
        EmailStatus.ACCEPT_ALL,
        EmailStatus.UNVERIFIED,
        EmailStatus.INVALID,
        EmailStatus.UNKNOWN,
        None,
    ],
)
def test_an_address_that_is_not_verified_is_never_a_match_key(
    status: EmailStatus | None,
) -> None:
    values: dict[str, Any] = {"person__email": "jane@example.com"}
    if status is not None:
        values["person__email_status"] = status
    assert keys_of(**values) == []


# Verifies: specs/lead-source-adapters/requirements.md#8.11
def test_unverified_and_accept_all_addresses_only_corroborate() -> None:
    for status in (EmailStatus.UNVERIFIED, EmailStatus.ACCEPT_ALL):
        found = extract_match_keys(
            contribution(person__email=" Jane@Example.com", person__email_status=status)
        )
        assert found.keys == ()
        assert found.corroborating_emails == frozenset({"jane@example.com"})


# Verifies: specs/lead-source-adapters/requirements.md#8.11
def test_a_verified_address_is_a_key_not_corroboration() -> None:
    found = extract_match_keys(
        contribution(
            person__email="jane@example.com", person__email_status=EmailStatus.VERIFIED
        )
    )
    assert found.corroborating_emails == frozenset()


# Verifies: specs/lead-source-adapters/requirements.md#8.2
@pytest.mark.parametrize("email", ["", "  ", "no-at-sign", "@example.com", "a@"])
def test_a_blank_or_malformed_verified_email_yields_no_key(email: str) -> None:
    assert keys_of(person__email=email, person__email_status=EmailStatus.VERIFIED) == []


# Verifies: specs/lead-source-adapters/requirements.md#8.3
def test_name_domain_key_folds_name_and_uses_registrable_domain() -> None:
    value = one_key(
        MatchKeyKind.NAME_DOMAIN,
        person__first_name=untrusted("  Jane "),
        person__last_name=untrusted("DOE   Smith"),
        company__domain="https://Mail.Example.co.uk/about",
    )
    assert value == "jane doe smith\x1fexample.co.uk"


# Verifies: specs/lead-source-adapters/requirements.md#8.3
def test_a_full_name_path_wins_over_first_and_last() -> None:
    value = one_key(
        MatchKeyKind.NAME_DOMAIN,
        person__full_name="Ada   Lovelace",
        person__first_name="Zed",
        person__last_name="Zed",
        company__domain="example.com",
    )
    assert value == "ada lovelace\x1fexample.com"


# Verifies: specs/lead-source-adapters/requirements.md#8.3
def test_each_registrable_domain_gives_one_name_key() -> None:
    found = keys_of(
        person__full_name="Ada Lovelace",
        company__domain=("old.org", "www.new.com", "shop.new.com"),
    )
    assert found == [
        (MatchKeyKind.NAME_DOMAIN, "ada lovelace\x1fnew.com"),
        (MatchKeyKind.NAME_DOMAIN, "ada lovelace\x1fold.org"),
    ]


# Verifies: specs/lead-source-adapters/requirements.md#8.3
@pytest.mark.parametrize(
    "values",
    [
        {"person__full_name": "Ada Lovelace"},
        {"company__domain": "example.com"},
        {"person__full_name": "  ", "company__domain": "example.com"},
        {"person__first_name": "Ada", "company__domain": "example.com"},
        {"person__full_name": "Ada Lovelace", "company__domain": "co.uk"},
        {"person__full_name": "Ada Lovelace", "company__domain": "localhost"},
        {"person__full_name": "Ada Lovelace", "company__domain": "10.0.0.1"},
        {"person__full_name": "Ada Lovelace", "company__domain": ""},
    ],
)
def test_name_alone_or_domain_alone_or_unusable_domain_yields_no_key(
    values: dict[str, Any],
) -> None:
    assert keys_of(**values) == []


# Verifies: specs/lead-source-adapters/requirements.md#8.3
def test_corroboration_attributes_are_exposed_for_the_key_three_gate() -> None:
    found = extract_match_keys(
        contribution(
            person__full_name="Ada Lovelace",
            person__title=untrusted("  VP   Sales "),
            company__name=untrusted("Analytical  ENGINES"),
            company__domain="example.com",
        )
    )
    assert found.titles == frozenset({"vp sales"})
    assert found.employers == frozenset({"analytical engines"})


# Verifies: specs/lead-source-adapters/requirements.md#8.3
@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ({"person__title": "VP Sales"}, {"person__title": " vp  sales"}, True),
        ({"company__name": "Acme Inc"}, {"company__name": "ACME INC"}, True),
        ({"person__title": "VP Sales"}, {"person__title": "CTO"}, False),
        ({"company__name": "Acme"}, {"company__name": "Beta"}, False),
        ({"person__title": "CTO"}, {"company__name": "CTO"}, False),
        ({}, {}, False),
    ],
)
def test_two_contributions_corroborate_on_shared_title_or_employer_only(
    a: dict[str, Any], b: dict[str, Any], expected: bool
) -> None:
    ka = extract_match_keys(contribution(person__full_name="A B", **a))
    kb = extract_match_keys(contribution(person__full_name="A B", **b))
    assert corroborates(ka, kb) is expected
    assert corroborates(kb, ka) is expected


# Verifies: specs/lead-source-adapters/requirements.md#8.1
def test_keys_come_out_in_durability_order() -> None:
    found = keys_of(
        person__full_name="Ada Lovelace",
        company__domain="example.com",
        person__email="ada@example.com",
        person__email_status=EmailStatus.VERIFIED,
        person__linkedin_url="https://linkedin.com/in/ada",
    )
    assert [kind for kind, _ in found] == [
        MatchKeyKind.LINKEDIN_URL,
        MatchKeyKind.VERIFIED_EMAIL,
        MatchKeyKind.NAME_DOMAIN,
    ]


# Verifies: specs/lead-source-adapters/requirements.md#8.1
def test_a_contribution_with_no_usable_key_yields_an_empty_result() -> None:
    found = extract_match_keys(contribution())
    assert found.keys == ()
    assert found.corroborating_emails == frozenset()


# Verifies: specs/lead-source-adapters/requirements.md#8.3
@pytest.mark.parametrize(
    "values",
    [
        {"person__linkedin_url": 5},
        {"person__email": 5, "person__email_status": EmailStatus.VERIFIED},
        {"person__full_name": 5, "company__domain": "a.com"},
        {"person__full_name": "A B", "company__domain": 5},
        {"person__full_name": "A B", "company__domain": ("a.com", 5)},
    ],
)
def test_a_non_text_value_is_a_type_error_naming_the_path_not_the_value(
    values: dict[str, Any],
) -> None:
    with pytest.raises(TypeError) as raised:
        extract_match_keys(contribution(**values))
    assert "5" not in str(raised.value).replace("person.", "").replace("company.", "")


# Verifies: specs/lead-source-adapters/requirements.md#8.1
def test_match_key_repr_withholds_the_personal_value() -> None:
    (key,) = extract_match_keys(
        contribution(person__linkedin_url="https://linkedin.com/in/secret-person")
    ).keys
    assert "secret-person" not in repr(key)
    assert "secret-person" not in str(key)


FULL: dict[str, Any] = {
    "person__linkedin_url": "https://LinkedIn.com/in/Ada/?x=1",
    "person__email": "Ada@Example.com ",
    "person__email_status": EmailStatus.VERIFIED,
    "person__first_name": "Ada",
    "person__last_name": "Lovelace",
    "person__title": "CTO",
    "company__name": "Engines",
    "company__domain": ("b.example.org", "a.example.com"),
}


# Verifies: specs/lead-source-adapters/requirements.md#8.8 (property)
@pytest.mark.parametrize("size", range(len(FULL) + 1))
def test_extraction_is_deterministic_and_independent_of_value_insertion_order(
    size: int,
) -> None:
    for subset in itertools.combinations(sorted(FULL), size):
        reference = extract_match_keys(contribution(**{k: FULL[k] for k in subset}))
        for perm in itertools.islice(itertools.permutations(subset), 24):
            again = extract_match_keys(contribution(**{k: FULL[k] for k in perm}))
            assert again == reference


# Verifies: specs/lead-source-adapters/requirements.md#8.8 (property)
def test_extraction_is_idempotent_over_its_own_key_values() -> None:
    first = extract_match_keys(contribution(**FULL))
    by_kind = {k.kind: k.value for k in first.keys}
    again = extract_match_keys(
        contribution(
            person__linkedin_url=by_kind[MatchKeyKind.LINKEDIN_URL],
            person__email=by_kind[MatchKeyKind.VERIFIED_EMAIL],
            person__email_status=EmailStatus.VERIFIED,
            person__full_name="ada lovelace",
            person__title="cto",
            company__name="engines",
            company__domain=("b.example.org", "a.example.com"),
        )
    )
    assert again == first
    assert extract_match_keys(contribution(**FULL)) == first


# Verifies: specs/lead-source-adapters/requirements.md#8.3
def test_a_name_in_composed_and_decomposed_unicode_gives_one_key() -> None:
    composed = one_key(
        MatchKeyKind.NAME_DOMAIN,
        person__full_name="José García",
        company__domain="example.com",
    )
    decomposed = one_key(
        MatchKeyKind.NAME_DOMAIN,
        person__full_name="José García",
        company__domain="example.com",
    )
    assert composed == decomposed


# Verifies: specs/lead-source-adapters/requirements.md#8.3
def test_a_name_with_non_breaking_space_tab_and_newline_collapses() -> None:
    value = one_key(
        MatchKeyKind.NAME_DOMAIN,
        person__full_name="Ada\N{NO-BREAK SPACE} \tLove\nlace",
        company__domain="example.com",
    )
    assert value == "ada love lace\x1fexample.com"


# Verifies: specs/lead-source-adapters/requirements.md#8.16
def test_a_domain_in_unicode_and_punycode_gives_one_name_key() -> None:
    unicode_form = one_key(
        MatchKeyKind.NAME_DOMAIN,
        person__full_name="Ada Lovelace",
        company__domain="www.München.de",
    )
    punycode = one_key(
        MatchKeyKind.NAME_DOMAIN,
        person__full_name="Ada Lovelace",
        company__domain="xn--mnchen-3ya.de",
    )
    assert unicode_form == punycode


# Verifies: specs/lead-source-adapters/requirements.md#8.2
def test_email_is_lowercased_not_casefolded_so_distinct_mailboxes_stay_apart() -> None:
    a = one_key(
        MatchKeyKind.VERIFIED_EMAIL,
        person__email="Straße@example.com",
        person__email_status=EmailStatus.VERIFIED,
    )
    b = one_key(
        MatchKeyKind.VERIFIED_EMAIL,
        person__email="strasse@example.com",
        person__email_status=EmailStatus.VERIFIED,
    )
    assert a != b
    assert a == "straße@example.com"


# Verifies: specs/lead-source-adapters/requirements.md#8.3
def test_two_tenants_of_a_shared_hosting_suffix_do_not_share_a_domain() -> None:
    one = keys_of(person__full_name="Ada Lovelace", company__domain="one.github.io")
    two = keys_of(person__full_name="Ada Lovelace", company__domain="two.github.io")
    assert one != two
    assert keys_of(person__full_name="Ada Lovelace", company__domain="github.io") == []


# Verifies: specs/lead-source-adapters/requirements.md#8.16
def test_extraction_reads_only_the_pinned_suffix_snapshot_and_opens_no_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import socket

    from leadforge.lead_ingestion import match_keys

    def refuse(*_: object, **__: object) -> None:
        raise AssertionError("match key extraction opened a socket")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    assert match_keys._PSL.suffix_list_urls == ()
    assert keys_of(person__full_name="Ada Lovelace", company__domain="example.co.uk")


# Verifies: specs/lead-source-adapters/requirements.md#8.1
def test_no_personal_value_appears_in_an_error_or_in_any_repr() -> None:
    class Sentinel:
        def __repr__(self) -> str:
            return "SENTINEL-PII"

        __str__ = __repr__

    with pytest.raises(TypeError) as raised:
        extract_match_keys(contribution(person__email=Sentinel()))
    assert "SENTINEL-PII" not in str(raised.value)
    assert "person.email" in str(raised.value)

    found = extract_match_keys(
        contribution(
            person__linkedin_url="https://linkedin.com/in/sentinel-pii",
            person__email="sentinel-pii@example.com",
            person__email_status=EmailStatus.VERIFIED,
            person__full_name="Sentinel Pii",
            person__title="Sentinel Title",
            company__name="Sentinel Corp",
            company__domain="sentinel-corp.com",
        )
    )
    assert "sentinel" not in repr(found).lower()
