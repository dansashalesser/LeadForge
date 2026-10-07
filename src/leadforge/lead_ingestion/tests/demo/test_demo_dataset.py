"""The demo dataset: committed tables, adapter-valid answers, and a full scored run.

The tables are checked against the seed (a stale file fails), every answer the demo
transport serves is validated by the adapter that will read it, and one run of the
real pipeline on the tables, sockets blocked, is scored against the answer key.
"""

import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from leadforge.lead_ingestion import cli
from leadforge.lead_ingestion.adapters.apollo import ApolloSource
from leadforge.lead_ingestion.adapters.google_search import GoogleSearchSource
from leadforge.lead_ingestion.adapters.hubspot import HubSpotSource
from leadforge.lead_ingestion.adapters.hunter import HunterSource
from leadforge.lead_ingestion.database import create_store_engine
from leadforge.lead_ingestion.demo import generator, outreach_data
from leadforge.lead_ingestion.demo.cli import DEMO_RETRY, PROFILE
from leadforge.lead_ingestion.demo.scorecard import load_active_leads, render, score
from leadforge.lead_ingestion.demo.transport import (
    DemoLog,
    DemoTransport,
    demo_transport_factory,
)
from leadforge.lead_ingestion.demo.transport import _Tables as Tables
from leadforge.lead_ingestion.ingest_runner import run_ingestion
from leadforge.lead_ingestion.match_key_digest import MATCH_KEY_SECRET_ENV
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.orchestrator import SourceStatus
from leadforge.lead_ingestion.registry import SourceRegistry
from leadforge.lead_ingestion.tests.socket_guard import SocketGuard, guard_for_mode

DISCOVERED = SourceRegistry.discover()
# Expectations the pipeline does not meet yet, each a reported finding, never
# edited into the key: duplicate Apollo records naming different primary domains
# merge but flag no tie; a verifier's invalid or accept_all verdict loses to
# another source's "verified" (projection's verified-first rule).
KNOWN_FAILING_CHECKS = frozenset(
    {
        ("duplicate_with_domain_conflict", "primary_domain_tie_flagged"),
        ("hunter_invalid", "email_status"),
        ("hunter_accept_all", "email_status"),
    }
)


@pytest.fixture
def clean_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    for name in DISCOVERED.names():
        for variable in DISCOVERED.source_class(name).required_env:
            monkeypatch.delenv(variable, raising=False)
    for variable in ("LEADFORGE_ENV_FILE", MATCH_KEY_SECRET_ENV):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("LEADFORGE_MODE", "synthetic")
    monkeypatch.chdir(tmp_path)
    database = tmp_path / "demo.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")
    return database


@pytest.fixture
def guard(
    clean_environment: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[SocketGuard]:
    blocked = guard_for_mode(DataMode.SYNTHETIC)
    blocked.install(monkeypatch)
    yield blocked
    blocked.assert_clean()


def test_the_committed_tables_are_what_the_seed_generates() -> None:
    built = generator.build()
    for name, table in built.items():
        assert generator.load(name) == json.loads(json.dumps(table)), name


def test_the_generator_is_deterministic() -> None:
    first = json.dumps(generator.build(), sort_keys=True)
    assert json.dumps(generator.build(), sort_keys=True) == first


def test_the_answer_key_covers_every_scenario() -> None:
    key = generator.load(generator.ANSWER_KEY)
    seen = {p["scenario"] for p in key["people"]}
    assert seen == set(generator.SCENARIO_NOTES) | set(outreach_data.WORKER_NOTES)
    assert len(key["people"]) >= 240
    assert len(key["companies"]) == 41  # the 40 of the base set and DataStax


def _transport(
    source: type[ApolloSource]
    | type[HubSpotSource]
    | type[HunterSource]
    | type[GoogleSearchSource],
    tables: Tables,
) -> DemoTransport:
    return DemoTransport(source.name, source.endpoints, tables=tables, log=DemoLog())


async def test_every_answer_the_demo_serves_passes_its_adapters_validation() -> None:
    tables = Tables(generator.DATA_DIR)
    apollo = _transport(ApolloSource, tables)
    for uid in ("cassandra", "datastax", "mongodb_atlas", "mongodb_realm"):
        page = await apollo.send(
            ApolloSource.endpoints["search"],
            params={
                "currently_using_any_of_technology_uids[]": uid,
                "page": 1,
                "per_page": 100,
            },
            json_body=None,
            headers={},
        )
        ApolloSource.validate_fixture("search", page.body)
    for record in tables.apollo_records:
        answer = await apollo.send(
            ApolloSource.endpoints["match"],
            params=None,
            json_body={"id": record["search_result"]["id"]},
            headers={},
        )
        ApolloSource.validate_fixture("match", answer.body)

    hubspot = _transport(HubSpotSource, tables)
    for contact in tables.contacts:
        email = contact["properties"]["email"]
        found = await hubspot.send(
            HubSpotSource.endpoints["contact_search"],
            params={"version": "2026-09"},
            json_body={
                "filterGroups": [
                    {
                        "filters": [
                            {"propertyName": "email", "operator": "EQ", "value": email}
                        ]
                    }
                ],
                "properties": ["email", "hs_email_optout", "lifecyclestage"],
                "limit": 100,
            },
            headers={},
        )
        HubSpotSource.validate_fixture("contact_search", found.body)
        deals = await hubspot.send(
            HubSpotSource.endpoints["deal_search"],
            params={"version": "2026-09"},
            json_body={
                "filterGroups": [
                    {
                        "filters": [
                            {
                                "propertyName": "associations.contact",
                                "operator": "EQ",
                                "value": contact["id"],
                            },
                            {
                                "propertyName": "hs_is_closed",
                                "operator": "EQ",
                                "value": "false",
                            },
                        ]
                    }
                ],
                "limit": 1,
            },
            headers={},
        )
        HubSpotSource.validate_fixture("deal_search", deals.body)

    hunter = _transport(HunterSource, tables)
    for domain in tables.hunter["domain_search"]:
        found = await hunter.send(
            HunterSource.endpoints["domain_search"],
            params={"domain": domain, "limit": 100},
            json_body=None,
            headers={},
        )
        HunterSource.validate_fixture("domain_search", found.body)
    for answer in tables.hunter["email_finder"]:
        HunterSource.validate_fixture("email_finder", answer)
    for answer in tables.hunter["email_verifier"].values():
        HunterSource.validate_fixture("email_verifier", answer)

    google = _transport(GoogleSearchSource, tables)
    for domain in tables.google["by_domain"]:
        page = await google.send(
            GoogleSearchSource.endpoints["search"],
            params={"engine": "google", "q": f'"{domain}" DataStax', "start": 0},
            json_body=None,
            headers={},
        )
        GoogleSearchSource.validate_fixture("search", page.body)


def test_the_demo_transport_refuses_live_mode() -> None:
    factory, _ = demo_transport_factory()
    with pytest.raises(ValueError, match="synthetic only"):
        factory(ApolloSource, DataMode.LIVE)


async def test_a_demo_run_meets_the_answer_key(
    clean_environment: Path, guard: SocketGuard
) -> None:
    factory, log = demo_transport_factory()

    outcome = await run_ingestion(
        target_profile_path=PROFILE,
        transport_factory=factory,
        synthetic_retry=DEMO_RETRY,
    )

    assert outcome.exit.exit_code == 0
    asked = {name.split(".")[0] for name in log.requests}
    assert asked == {"apollo", "hubspot", "hunter", "google_search"}
    # The scripted 429 is retried and Apollo still succeeds.
    assert log.requests["apollo.match_429"] == 1
    apollo = [r for r in outcome.results if r.source_name == "apollo"]
    assert all(r.outcome.status is SourceStatus.OK for r in apollo)
    assert sum(r.outcome.retries for r in apollo) >= 1
    # Coverage first: the query cap leaves no company unsearched.
    assert log.searched_domains >= set(
        generator.load(generator.ANSWER_KEY)["companies"]
    )
    engine = create_store_engine(f"sqlite:///{clean_environment}")
    try:
        with Session(engine) as session:
            leads = load_active_leads(session)
    finally:
        engine.dispose()
    card = score(leads, generator.load(generator.ANSWER_KEY), log.as_json())
    failing = {(s, c) for s, r in card.scenarios.items() for c in r.failed}
    assert failing <= KNOWN_FAILING_CHECKS, render(card)
    assert card.checks_passed > 1600
    # Duplicates kept on purpose only: HubSpot answers it could not tie to one person
    # and verdicts on shared addresses.
    assert card.leads_without_subject <= 20


def test_the_demo_commands_run_and_score(
    clean_environment: Path, guard: SocketGuard
) -> None:
    url = f"sqlite:///{clean_environment}"
    runner = CliRunner()

    ran = runner.invoke(cli.app, ["demo", "run", "--database-url", url])
    scored = runner.invoke(cli.app, ["demo", "score", "--database-url", url])

    assert ran.exit_code == 0, ran.output
    assert "exit code: 0" in ran.output
    assert scored.exit_code == 0, scored.output
    assert "checks:" in scored.output
    assert "never asked" not in scored.output
    # Scenario counts only, never an address.
    assert not re.search(r"[\w.+-]+@[\w-]+\.\w+", scored.output)
