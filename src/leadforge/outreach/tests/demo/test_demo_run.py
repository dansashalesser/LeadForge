"""The demo dataset, scored; and a zero-key run in every mode (15.1, 15.2, 15.5)."""

# ruff: noqa: F811 - fixtures imported from the ingestion tests

import json
from collections import Counter
from datetime import timedelta
from functools import partial
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.catalog import load_catalog
from leadforge.lead_ingestion.demo import generator
from leadforge.lead_ingestion.demo.cli import DEMO_RETRY
from leadforge.lead_ingestion.demo.outreach_data import TARGET_DOMAIN
from leadforge.lead_ingestion.demo.transport import demo_transport_factory
from leadforge.lead_ingestion.ingest_runner import run_ingestion
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.tests.socket_guard import guard_for_mode
from leadforge.lead_ingestion.tests.test_persistence_both_engines import (  # noqa: F401 - fixtures
    Backend,
    backend,
    blank,
    postgres_url,
)
from leadforge.lead_ingestion.tests.test_run_lifecycle_both_engines import (  # noqa: F401 - fixtures
    composed,
)
from leadforge.outreach.acceptance import AnswerKeyAcceptance, SeededAcceptance
from leadforge.outreach.clock import FakeClock
from leadforge.outreach.config import load_outreach_config
from leadforge.outreach.dispatch import DryRunDispatcher
from leadforge.outreach.report import build_report
from leadforge.outreach.scorecard import render, score_decisions
from leadforge.outreach.search_plan import parse_request
from leadforge.outreach.service import SearchService, SearchSummary
from leadforge.outreach.tests.support import NOW
from leadforge.outreach.tick import tick

CONFIG_DIR = Path(__file__).resolve().parents[5] / "config"
KEY = generator.load(generator.ANSWER_KEY)


# --------------------------------------------------------------------- 15.1


# Verifies: outreach requirements 15.1
def test_the_answer_key_lists_a_person_for_every_outcome() -> None:
    outcomes = Counter(p["outreach"]["decision"] for p in KEY["people"])
    sequences = Counter(
        p["outreach"].get("sequence")
        for p in KEY["people"]
        if p["outreach"]["decision"] == "selected"
    )

    assert {"selected", "rejected", "needs_enrichment", "absent"} <= set(outcomes)
    assert {"email", "fallback_email", "stalled"} <= set(sequences)
    accepted = [p for p in KEY["people"] if p["outreach"].get("accepts_after_days")]
    ignored = [
        p
        for p in KEY["people"]
        if p["outreach"]["decision"] == "selected"
        and not p["outreach"].get("accepts_after_days")
    ]
    assert accepted
    assert ignored


# Verifies: outreach requirements 15.1
def test_the_target_company_has_workers_of_every_kind() -> None:
    workers = [p for p in KEY["people"] if p["company"] == TARGET_DOMAIN]

    assert {p["scenario"] for p in workers} == {
        "worker_accepts",
        "worker_ignores",
        "worker_ignores_no_email",
        "worker_no_linkedin",
        "worker_opted_out",
        "worker_customer",
        "vendor_staff",
    }
    no_linkedin = [p for p in workers if p["scenario"] == "worker_no_linkedin"]
    assert no_linkedin
    assert all(p["expect"]["linkedin_url"] is None for p in no_linkedin)
    assert all(p["expect"]["email"] for p in no_linkedin)


# --------------------------------------------------------------- a demo search


def _service(
    backend: Backend, tmp_path: Path, *, key_acceptance: bool = True
) -> SearchService:
    config = load_outreach_config(CONFIG_DIR / "outreach.yaml")
    factory, _ = demo_transport_factory()
    return SearchService(
        config=config,
        catalog=load_catalog(),
        environ={},
        clock=FakeClock(NOW),
        acceptance=(
            AnswerKeyAcceptance(
                KEY,
                partial(Session, backend.engine),
                SeededAcceptance(config.simulation),
            )
            if key_acceptance
            else SeededAcceptance(config.simulation)
        ),
        dispatcher=DryRunDispatcher(tmp_path / "outbox.jsonl", lambda _: None),
        engine=backend.engine,
        ingest=partial(
            run_ingestion, transport_factory=factory, synthetic_retry=DEMO_RETRY
        ),
    )


async def _search(
    backend: Backend, tmp_path: Path, mode: str, query: str
) -> SearchSummary:
    service = _service(backend, tmp_path)
    plan = service.plan(
        parse_request(mode, query, products=[query] if mode == "users" else [])
    )
    summary = await service.run(plan, show=lambda _: None)
    config = load_outreach_config(CONFIG_DIR / "outreach.yaml")
    dispatcher = DryRunDispatcher(tmp_path / "outbox.jsonl", lambda _: None)
    accept = AnswerKeyAcceptance(
        KEY, partial(Session, backend.engine), SeededAcceptance(config.simulation)
    )
    for days in (1, 2, 3, 4, 5, 6, 12):
        with Session(backend.engine) as session, session.begin():
            tick(
                session,
                summary.search_id,
                NOW + timedelta(days=days),
                cfg=config.triggers,
                acceptance=accept,
                dispatcher=dispatcher,
            )
    return summary


# Verifies: outreach requirements 15.2
async def test_the_scorecard_prints_a_mismatch_count_per_outcome_and_workers_match(
    composed: Backend, tmp_path: Path
) -> None:
    summary = await _search(composed, tmp_path, "workers", "DataStax")

    with Session(composed.engine) as session:
        card = score_decisions(session, summary.search_id, KEY)
    text = render(card)

    for outcome in ("selected", "rejected", "needs_enrichment"):
        assert outcome in text
    assert "mismatched" in text
    assert f"mismatches: {card.mismatches}" in text
    assert card.decisions["needs_enrichment"].gathered == 2
    assert card.decisions["rejected"].gathered == 2
    assert card.decisions["selected"].gathered == 8  # six workers, two vendor_staff
    assert card.mismatches == 0, text
    assert {k: v.gathered for k, v in card.sequences.items()} == {
        "email": 3,
        "fallback_email": 4,  # two worker_ignores, two vendor_staff
        "stalled": 1,
    }


# Verifies: outreach requirements 3.1
# Verifies: outreach requirements 3.2
async def test_a_workers_run_asks_for_a_domain_and_records_hunter_domain_search(
    composed: Backend, tmp_path: Path
) -> None:
    service = _service(composed, tmp_path)
    plan = service.plan(parse_request("workers", "DataStax"))
    assert plan.domains == (TARGET_DOMAIN,)
    summary = await service.run(plan, show=lambda _: None)

    with Session(composed.engine) as session:
        notes = build_report(session, summary.search_id).notes
        sources = {n.split(":")[0] for n in notes if n.startswith("source")}
    assert {"source apollo", "source hunter"} <= sources


# --------------------------------------------------------------------- 15.5


@pytest.mark.parametrize(
    ("mode", "query"),
    [
        ("free_text", "teams running cassandra and datastax"),
        ("workers", "DataStax"),
        ("users", "DataStax"),
    ],
)
# Verifies: outreach requirements 15.5
# Verifies: outreach requirements 14.3
async def test_a_zero_key_run_completes_end_to_end_in_every_mode_with_sockets_blocked(
    composed: Backend,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    query: str,
) -> None:
    guard = guard_for_mode(DataMode.SYNTHETIC)
    if composed.name == "sqlite":  # a Postgres server is a socket by nature
        guard.install(monkeypatch)

    summary = await _search(composed, tmp_path, mode, query)

    guard.assert_clean()
    with Session(composed.engine) as session:
        report = build_report(session, summary.search_id)
    assert report.funnel.gathered > 0
    assert report.funnel.selected > 0
    assert report.funnel.invited == report.funnel.selected
    assert report.funnel.emailed + report.funnel.stalled == report.funnel.invited
    assert "compiler: offline" in report.notes or mode != "free_text"
    assert "judge: off" in report.notes
    assert all("live" not in n for n in report.notes)
    lines = [
        json.loads(x) for x in (tmp_path / "outbox.jsonl").read_text().splitlines()
    ]
    assert {line["state"] for line in lines} == {"dry_run"}
    assert any(line["kind"] == "invite" for line in lines)
