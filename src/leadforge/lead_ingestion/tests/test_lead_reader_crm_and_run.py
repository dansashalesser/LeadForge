"""CRM state and run scope on the lead reader (outreach seam 1.2, 1.3)."""

# ruff: noqa: F811 - fixtures imported from test_persistence_both_engines

import uuid
from datetime import timedelta

import sqlalchemy as sa
from sqlalchemy.orm import Session

from leadforge.lead_ingestion import ingest_runner
from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.store.lead_reader import CrmState, crm_state, list_leads
from leadforge.lead_ingestion.tests.test_lead_reader import merge, only
from leadforge.lead_ingestion.tests.test_lead_remerge import NOW, ada
from leadforge.lead_ingestion.tests.test_persistence_both_engines import (  # noqa: F401 - fixtures
    Backend,
    backend,
    blank,
    postgres_url,
)
from leadforge.lead_ingestion.tests.test_run_lifecycle_both_engines import (  # noqa: F401 - fixtures
    PROFILE,
    composed,
)


# Verifies: outreach requirements 6.3
def test_crm_state_reads_the_stored_crm_contribution_fields(backend: Backend) -> None:
    lead = only(
        backend.engine,
        [
            ada(
                "alpha",
                crm__contact_exists=True,
                crm__lifecycle_stage="customer",
                crm__has_open_deal=False,
            ),
        ],
    )
    with Session(backend.engine) as session:
        state = crm_state(session, [lead])

    assert state == {
        lead: CrmState(
            contact_exists=True, lifecycle_stage="customer", has_open_deal=False
        )
    }


# Verifies: outreach requirements 6.3
def test_crm_state_takes_the_newest_value_and_is_empty_without_crm(
    backend: Backend,
) -> None:
    ((old, _),) = merge(
        backend.engine, [ada("alpha", at=NOW, crm__lifecycle_stage="lead")]
    )
    merge(
        backend.engine,
        [ada("alpha", at=NOW + timedelta(days=1), crm__lifecycle_stage="customer")],
        hours=24,
    )
    stranger = uuid.uuid4()
    with Session(backend.engine) as session:
        state = crm_state(session, [old, stranger])
        nothing = crm_state(session, [])

    assert state[old].lifecycle_stage == "customer"
    assert state[stranger] == CrmState()
    assert nothing == {}


# Verifies: outreach requirements 5.2
def test_list_leads_is_scoped_to_the_leads_a_run_contributed_to(
    backend: Backend,
) -> None:
    lead = only(backend.engine, [ada("alpha")])
    with Session(backend.engine) as session:
        run_id = session.scalar(sa.select(m.SourceRun.run_id))
        assert run_id is not None
        scoped = list_leads(session, run_id=run_id)
        other = list_leads(session, run_id=uuid.uuid4())

    assert [x.lead_id for x in scoped] == [lead]
    assert other == ()


# Verifies: design risk R2 (CRM fields reachable in the stored fixtures)
async def test_crm_fields_are_reachable_after_a_fixture_run(composed: Backend) -> None:
    outcome = await ingest_runner.run_ingestion(target_profile=PROFILE)

    with Session(composed.engine) as session:
        leads = list_leads(session, run_id=outcome.run_id, limit=500)
        states = crm_state(session, [x.lead_id for x in leads])

    assert leads
    assert any(s.contact_exists for s in states.values())
    assert any(s.lifecycle_stage for s in states.values())
    assert any(s.has_open_deal is not None for s in states.values())
