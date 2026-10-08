"""Hunter keeps the LinkedIn, department, seniority and source metadata it returns."""

import json
from pathlib import Path
from typing import Any

from leadforge.lead_ingestion.adapters.hunter import HunterSource
from leadforge.lead_ingestion.base_source import LeadContribution, RawBatch
from leadforge.lead_ingestion.demo import generator
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.normalizer import unmapped_raw_paths

FIXTURES = Path(__file__).parents[2] / "fixtures" / "hunter"
SOURCE_META = "person.email_sources_meta"


def _contributions(body: dict[str, Any]) -> list[LeadContribution]:
    batch = RawBatch(
        source_name="hunter",
        payload={
            "searches": [{"domain": body["data"]["domain"], "response": body}],
            "credits_billable": True,
        },
    )
    transport = HunterSource.build_transport(DataMode.SYNTHETIC)
    source = HunterSource(DataMode.SYNTHETIC, transport=transport, environ={})
    return source.normalize_checked(batch)


def _fixture() -> dict[str, Any]:
    return json.loads((FIXTURES / "domain_search.json").read_text())


# Verifies: specs/user-recognition/requirements.md#3.3
def test_domain_search_keeps_linkedin_department_seniority_and_source_metadata() -> (
    None
):
    first, second = _contributions(_fixture())
    assert (
        first.values["person.email_linkedin_url"]
        == "https://www.linkedin.com/in/ada-lovelace"
    )
    assert first.values["person.departments"] == ("it",)
    assert first.values["person.seniority"] == "executive"
    assert first.values[SOURCE_META] == (
        {
            "uri": "https://example.com/team",
            "domain": "example.com",
            "extracted_on": "2026-01-10",
            "last_seen_on": "2026-09-01",
            "still_on_page": True,
        },
    )
    # Fields Hunter sent as null or empty are not contributed.
    for path in (
        "person.email_linkedin_url",
        "person.departments",
        "person.seniority",
        SOURCE_META,
    ):
        assert path not in second.values


# Verifies: specs/user-recognition/requirements.md#3.3
def test_source_metadata_has_no_nulls_inside_and_none_of_it_is_untrusted() -> None:
    body = _fixture()
    body["data"]["emails"][0]["sources"][0]["last_seen_on"] = None
    first = _contributions(body)[0]
    assert "last_seen_on" not in first.values[SOURCE_META][0]
    untrusted = {p.canonical_path for p in first.provenance if p.untrusted}
    assert untrusted.isdisjoint(
        {
            SOURCE_META,
            "person.email_linkedin_url",
            "person.departments",
            "person.seniority",
        }
    )


# Verifies: specs/user-recognition/requirements.md#3.3
def test_the_four_fields_are_no_longer_ignored() -> None:
    gone = {"email.seniority", "email.department", "email.linkedin"}
    assert not gone & HunterSource.IGNORED
    data = _fixture()["data"]
    wrapper = {
        **{k: v for k, v in data.items() if k != "emails"},
        "email": data["emails"][0],
    }
    assert unmapped_raw_paths(wrapper, HunterSource.RULES, HunterSource.IGNORED) == []


# Verifies: specs/user-recognition/requirements.md#3.3
def test_demo_people_carry_the_metadata_hunter_returns() -> None:
    searches = generator.load("hunter")["domain_search"].values()
    named = [e for s in searches for e in s["data"]["emails"] if e["first_name"]]
    assert named
    linked = 0
    for entry in named:
        [contribution] = [
            c
            for c in _contributions(
                {"data": {"domain": "d.test", "emails": [entry]}, "meta": {}}
            )
        ]
        for path in ("person.departments", "person.seniority", SOURCE_META):
            assert path in contribution.values
        linked += "person.email_linkedin_url" in contribution.values
    assert linked
