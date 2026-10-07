"""A search is stored before any provider request and linked to its run (5.1, 5.2)."""

# ruff: noqa: F811 - fixtures imported from the ingestion tests

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from leadforge.lead_ingestion import ingest_runner
from leadforge.lead_ingestion.base_source import BaseLeadSource
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.target_profile import TargetProfile, load_target_profile
from leadforge.lead_ingestion.tests.test_persistence_both_engines import (  # noqa: F401 - fixtures
    Backend,
    backend,
    blank,
    postgres_url,
)
from leadforge.lead_ingestion.tests.test_run_lifecycle_both_engines import (  # noqa: F401 - fixtures
    composed,
)
from leadforge.lead_ingestion.transport import Transport
from leadforge.outreach.company_plans import workers_plan
from leadforge.outreach.company_terms import CompanyTerms
from leadforge.outreach.config import load_outreach_config
from leadforge.outreach.profile import plan_to_profile
from leadforge.outreach.search_plan import SearchPlan, parse_request
from leadforge.outreach.searches import finish_search, link_hook, link_run, start_search
from leadforge.outreach.tables import OutreachSearch

CONFIG = Path(__file__).resolve().parents[4] / "config"
NOW = datetime(2026, 10, 7, tzinfo=UTC)


def _plan() -> SearchPlan:
    return workers_plan(
        parse_request("workers", "Acme", ["acme.com"]), CompanyTerms(companies={})
    )


# Verifies: outreach requirements 5.1
def test_a_search_row_holds_mode_query_plan_compiler_and_start_time(
    backend: Backend,
) -> None:
    plan = _plan()
    with Session(backend.engine) as session, session.begin():
        search_id = start_search(session, plan, now=NOW)

    with Session(backend.engine) as session:
        row = session.get_one(OutreachSearch, search_id)
        assert (row.mode, row.query, row.compiler) == ("workers", "Acme", "offline")
        assert SearchPlan.model_validate(row.plan) == plan
        assert row.created_at.replace(tzinfo=UTC) == NOW
        assert (row.status, row.ingestion_run_id) == ("planned", None)


# Verifies: outreach requirements 5.2
def test_a_search_links_one_run_and_closes(backend: Backend) -> None:
    with Session(backend.engine) as session, session.begin():
        search_id = start_search(session, _plan(), now=NOW)
        run = m.IngestionRun(started_at=NOW, status="running")
        session.add(run)
        session.flush()
        link_run(session, search_id, run.id)
        with pytest.raises(ValueError, match="already linked"):
            link_run(session, search_id, run.id)
        finish_search(session, search_id, "done")
        row = session.get_one(OutreachSearch, search_id)
        assert (row.status, row.ingestion_run_id) == ("done", run.id)


# Verifies: outreach requirements 5.1
# Verifies: outreach requirements 5.2
# Verifies: outreach requirements 1.2
async def test_the_search_is_stored_and_linked_before_the_first_provider_call(
    composed: Backend,
) -> None:
    config = load_outreach_config(CONFIG / "outreach.yaml")
    profile = plan_to_profile(
        _plan(), load_target_profile(CONFIG / "target_profile.yaml"), config.sources
    )
    with Session(composed.engine) as session, session.begin():
        search_id = start_search(session, _plan(), now=NOW)
    events: list[tuple[str, uuid.UUID | None]] = []
    link = link_hook(composed.engine, search_id)

    def on_started(run_id: uuid.UUID) -> None:
        link(run_id)
        events.append(("linked", run_id))

    def transport(source_class: type[BaseLeadSource], mode: DataMode) -> Transport:
        events.append(("transport", None))
        return source_class.build_transport(mode)

    outcome = await ingest_runner.run_ingestion(
        target_profile=profile,
        on_run_started=on_started,
        transport_factory=transport,
    )

    assert events[0] == ("linked", outcome.run_id)
    assert ("transport", None) in events
    with Session(composed.engine) as session:
        row = session.get_one(OutreachSearch, search_id)
        assert (row.ingestion_run_id, row.status) == (outcome.run_id, "running")


# Verifies: outreach requirements 5.2
async def test_a_failing_link_aborts_the_run_before_any_source_runs(
    composed: Backend,
) -> None:
    def refuse(run_id: uuid.UUID) -> None:
        raise RuntimeError("no link")

    with pytest.raises(RuntimeError, match="no link"):
        await ingest_runner.run_ingestion(
            target_profile=TargetProfile(), on_run_started=refuse
        )

    with Session(composed.engine) as session:
        run = session.scalars(sa.select(m.IngestionRun)).one()
        assert run.status == "aborted"
        assert run.failure_reason == "on_run_started: RuntimeError"
        assert session.scalar(sa.select(sa.func.count(m.SourceContribution.id))) == 0
