"""Apollo keeps the signals it already returns (user-recognition 3.1, 3.2)."""

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from leadforge.lead_ingestion.adapters.apollo import ApolloSource
from leadforge.lead_ingestion.base_source import Endpoint, SourceRequest
from leadforge.lead_ingestion.demo import generator
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.normalizer import NormalizationContext, Normalizer
from leadforge.lead_ingestion.transport import TransportResponse

UID_PARAM = "currently_using_any_of_technology_uids[]"
FIXTURE_DIR = Path(__file__).parents[2] / "fixtures" / "apollo"
KEPT_PERSON = ("headline", "departments", "functions", "seniority")
KEPT_ORG = ("technology_names", "keywords")


class _ByUid:
    """Search transport answering each technology UID with its own people."""

    def __init__(self, by_uid: Mapping[str, list[str]]) -> None:
        self.by_uid = by_uid

    async def send(
        self,
        endpoint: Endpoint,
        *,
        params: Mapping[str, object] | None,
        json_body: Mapping[str, object] | None,
        headers: Mapping[str, str],
    ) -> TransportResponse:
        ids = self.by_uid[str((params or {})[UID_PARAM])]
        body = {
            "people": [
                {"id": i, "first_name": "A", "last_name_obfuscated": "B***"}
                for i in ids
            ]
        }
        return TransportResponse(status=200, headers={}, body=body)


def _context() -> NormalizationContext:
    return NormalizationContext(
        source_name="apollo",
        data_mode=DataMode.SYNTHETIC,
        fetched_at=datetime.now(UTC),
        answerable_surfaces=ApolloSource.answerable_surfaces,
    )


def _match_values(body: Mapping[str, Any]) -> Mapping[str, object]:
    return Normalizer().apply(body, ApolloSource.MATCH_RULES, _context()).values


def _demo_enrichments() -> list[Mapping[str, Any]]:
    return [r["enrichment"] for r in generator.load("apollo")["records"]]


# Verifies: specs/user-recognition/requirements.md#3.1
async def test_a_person_found_by_two_uid_searches_carries_both() -> None:
    transport = _ByUid({"datastax": ["p1", "p2"], "cassandra": ["p1", "p3"]})
    source = ApolloSource(
        DataMode.SYNTHETIC,
        transport=transport,
        vocabulary={"a": ["datastax"], "b": ["cassandra"]},
    )
    batch = await source.fetch_raw(SourceRequest(kind="discovery"))
    people = {p["id"]: p for p in batch.payload["people"]}
    assert set(people) == {"p1", "p2", "p3"}
    assert people["p1"]["matched_technology_uids"] == ["datastax", "cassandra"]
    assert people["p2"]["matched_technology_uids"] == ["datastax"]
    assert people["p3"]["matched_technology_uids"] == ["cassandra"]
    values = [
        c.values["person.matched_technology_uids"] for c in source.normalize(batch)
    ]
    assert ["datastax", "cassandra"] in values


# Verifies: specs/user-recognition/requirements.md#3.2
def test_match_answer_person_and_organization_fields_are_mapped() -> None:
    body = {
        "person": {
            "id": "x",
            "match_confidence": "high",
            "headline": "Head of Data",
            "departments": ["data_science"],
            "functions": ["engineering"],
            "seniority": "director",
            "employment_history": [
                {
                    "organization_name": "Old Co",
                    "current": False,
                    "end_date": "2025-01-01",
                }
            ],
            "organization": {
                "technology_names": ["DataStax"],
                "keywords": ["data platform"],
            },
        }
    }
    values = _match_values(body)
    assert values["person.headline"].value == "Head of Data"
    assert values["person.departments"] == ["data_science"]
    assert values["person.functions"] == ["engineering"]
    assert values["person.seniority"] == "director"
    job = values["person.employment_history"][0]
    assert job["organization_name"] == "Old Co"
    assert "kind" not in job  # null fields are not stored
    assert values["company.technology_names"] == ["DataStax"]
    assert values["company.keywords"] == ["data platform"]


# Verifies: specs/user-recognition/requirements.md#3.2
def test_the_demo_replica_has_no_unmapped_kept_field() -> None:
    for answer in _demo_enrichments():
        unmapped = ApolloSource.unmapped_fixture_paths("match", answer)
        for field in (*KEPT_PERSON, "employment_history"):
            assert f"person.{field}" not in unmapped
        for field in KEPT_ORG:
            assert f"person.organization.{field}" not in unmapped


# Verifies: specs/user-recognition/requirements.md#3.2
def test_the_committed_match_fixture_carries_the_kept_fields() -> None:
    body = json.loads((FIXTURE_DIR / "match.json").read_text(encoding="utf-8"))
    ApolloSource.validate_fixture("match", body)
    assert ApolloSource.unmapped_fixture_paths("match", body) == []
    person = body["person"]
    assert all(f in person for f in (*KEPT_PERSON, "employment_history"))
    assert all(f in person["organization"] for f in KEPT_ORG)


# Verifies: specs/user-recognition/requirements.md#3.2
def test_a_demo_person_who_left_names_the_company_they_left() -> None:
    left = 0
    for answer in _demo_enrichments():
        person = answer["person"]
        history = person.get("employment_history") or []
        for job in history:
            if job["current"] is False:
                left += 1
                assert job["organization_name"] == person["organization"]["name"]
                assert job["end_date"]
    assert left > 0
