"""The Lead's role-address fields are persisted (follow-up 2026-10-06), on both engines.

``CanonicalLead.email_is_role_address`` and ``role_contact_emails`` (user decision
2026-10-06: a role address is never discarded) are stored on ``canonical_lead``
(migration 0009), loaded back through ``store.lead_reader`` (follow-up 2026-10-06:
the read path, not the raw row), and part of the lead's content: a change in either
is an update of the same lead (never a second lead) that the run logs, and an unchanged
re-run writes nothing. ``role_contact_emails`` holds email addresses: personal data,
never in a log line or the run report text.
"""

# ruff: noqa: F811 - fixtures imported from test_persistence_both_engines

import dataclasses
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session
from structlog.testing import capture_logs
from typer.testing import CliRunner

from leadforge.lead_ingestion import cli
from leadforge.lead_ingestion.clustering import IdentityCluster
from leadforge.lead_ingestion.ingest_runner import IngestionOutcome, run_ingestion
from leadforge.lead_ingestion.projection import project_lead
from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.store.lead_reader import StoredLead, list_leads
from leadforge.lead_ingestion.store.merged_leads import MergeStored, persist_merge
from leadforge.lead_ingestion.store.raw_responses import RetentionPolicy
from leadforge.lead_ingestion.tests.store_run_support import (
    Script,
    person,
    registry_of,
    scripted_source,
)
from leadforge.lead_ingestion.tests.test_end_to_end_zero_credential import (  # noqa: F401 - fixtures
    restore_structlog,
)
from leadforge.lead_ingestion.tests.test_lead_remerge import (
    NOW,
    RANKS,
    batches_of,
    contribution,
    start_run,
)
from leadforge.lead_ingestion.tests.test_persistence_both_engines import (  # noqa: F401 - fixtures
    Backend,
    _alembic,
    backend,
    blank,
    postgres_url,
)
from leadforge.lead_ingestion.tests.test_remerge_migration import (
    CANONICAL_LEAD_BEFORE_0009,
)
from leadforge.lead_ingestion.tests.test_run_lifecycle_both_engines import (  # noqa: F401 - fixtures
    composed,
)

# A title with the name and domain makes one person of a personal and a role record.
ACME = {"company__domain": "acme.com", "person__title": "CTO"}
ADA = person("ada@acme.com", "Ada Lovelace", **ACME)
ADA_INFO = person("info@acme.com", "Ada Lovelace", **ACME)
ADA_SALES = person("sales@acme.com", "Ada Lovelace", **ACME)
GRACE_ONLY_ROLE = person("info@navy.mil", "Grace Hopper", company__domain="navy.mil")
ROLE_ADDRESSES = ("info@acme.com", "sales@acme.com", "info@navy.mil")


def _leads(backend: Backend) -> dict[str, StoredLead]:
    """Every active lead, loaded through the read path, by name."""
    with Session(backend.engine) as s:
        leads = list_leads(s, limit=1000)
    by_name = {str(x.lead.full_name): x for x in leads}
    assert len(by_name) == len(leads)  # one lead per person: none duplicated
    return by_name


def _rows(backend: Backend) -> dict[str, m.CanonicalLeadRow]:
    """The raw rows: only for what a migration leaves in the column itself."""
    with Session(backend.engine) as s:
        rows = s.scalars(sa.select(m.CanonicalLeadRow)).all()
    by_name = {str(r.full_name): r for r in rows}
    assert len(by_name) == len(rows)
    return by_name


def _merge_lines(logs: Sequence[Mapping[str, Any]]) -> int:
    return sum(1 for e in logs if e.get("event") == "lead_merge")


def _assert_no_role_address(logs: Sequence[Mapping[str, Any]], text: str) -> None:
    for address in ROLE_ADDRESSES:
        assert address not in text
        assert all(address not in repr(entry) for entry in logs)


# Verifies: specs/lead-source-adapters/requirements.md#8.4
# Verifies: specs/lead-source-adapters/requirements.md#9.5
async def test_role_fields_round_trip_and_a_changed_contact_updates_the_same_lead(
    composed: Backend,
) -> None:
    def sources(beta: dict[str, Any]) -> Any:
        return registry_of(
            scripted_source("alpha", Script([ADA, GRACE_ONLY_ROLE])),
            scripted_source("beta", Script([beta])),
        )

    with capture_logs() as first_logs:
        first = await run_ingestion(registry=sources(ADA_INFO))
    leads = _leads(composed)
    ada, grace = leads["Ada Lovelace"].lead, leads["Grace Hopper"].lead
    assert (ada.email, ada.email_is_role_address) == ("ada@acme.com", False)
    assert ada.role_contact_emails == ("info@acme.com",)
    assert (grace.email, grace.email_is_role_address) == ("info@navy.mil", True)
    assert grace.role_contact_emails == ()
    _assert_no_role_address(first_logs, first.report_text)

    # Beta now reports another role contact: the same lead is updated and logged.
    with capture_logs() as second_logs:
        second = await run_ingestion(registry=sources(ADA_SALES))
    after = _leads(composed)
    assert after.keys() == leads.keys()  # no duplicated lead
    assert after["Ada Lovelace"].lead_id == leads["Ada Lovelace"].lead_id
    assert after["Ada Lovelace"].lead.role_contact_emails == (
        "info@acme.com",
        "sales@acme.com",
    )
    untouched = leads["Grace Hopper"].computed_at
    assert after["Grace Hopper"].computed_at == untouched
    assert _merge_lines(second_logs) == 1
    _assert_no_role_address(second_logs, second.report_text)

    # An unchanged re-run writes nothing and logs nothing.
    with capture_logs() as third_logs:
        third = await run_ingestion(registry=sources(ADA_SALES))
    assert third.stored.canonical_leads == 0
    assert _merge_lines(third_logs) == 0
    assert {n: x.computed_at for n, x in _leads(composed).items()} == {
        n: x.computed_at for n, x in after.items()
    }


SENTINEL = {"company__domain": "role-sentinel-5c1e.org", "person__title": "CTO"}
SENTINEL_ROLES = ("info@role-sentinel-5c1e.org", "sales@role-sentinel-5c1e.org")


# Verifies: specs/lead-source-adapters/requirements.md#22.1
def test_the_ingest_command_never_prints_a_role_address(
    composed: Backend, monkeypatch: pytest.MonkeyPatch, restore_structlog: None
) -> None:
    """Through the real ``ingest`` command and its log chain: the role addresses
    are stored (one as a lead's email, one as a contact) and never printed."""
    registry = registry_of(
        scripted_source(
            "alpha",
            Script(
                [
                    person("ada@role-sentinel-5c1e.org", "Ada Lovelace", **SENTINEL),
                    person(SENTINEL_ROLES[0], "Ada Lovelace", **SENTINEL),
                    person(SENTINEL_ROLES[1], "Grace Hopper", **SENTINEL),
                ]
            ),
        )
    )

    async def with_registry() -> IngestionOutcome:
        return await run_ingestion(registry=registry)

    monkeypatch.setattr(cli, "run_ingestion", with_registry)
    result = CliRunner().invoke(cli.app, ["ingest"])

    assert result.exit_code == 0, result.output
    leads = _leads(composed)
    assert leads["Ada Lovelace"].lead.role_contact_emails == (SENTINEL_ROLES[0],)
    grace = leads["Grace Hopper"].lead
    assert (grace.email, grace.email_is_role_address) == (SENTINEL_ROLES[1], True)
    assert "lead_merge" in result.stderr  # the log chain ran and logged the leads
    for text in (result.stdout, result.stderr):
        assert "role-sentinel-5c1e" not in text


def _persist_ada(backend: Backend, **lead_update: Any) -> MergeStored:
    """Persist Ada's one-contribution cluster, its lead updated by ``lead_update``."""
    ada = contribution(
        "alpha",
        person__email="ada@acme.com",
        person__email_status="verified",
        person__full_name="Ada Lovelace",
    )
    cluster = IdentityCluster("c", (ada,))
    result = project_lead(cluster, RANKS)
    assert result.lead is not None
    lead = result.lead.model_copy(update=lead_update)
    with Session(backend.engine) as s, s.begin():
        return persist_merge(
            s,
            run_id=start_run(s),
            batches=batches_of(ada),
            merged=[(cluster, dataclasses.replace(result, lead=lead))],
            computed_at=NOW,
            projection_version=1,
            projection_fingerprint="f" * 64,
            retention=RetentionPolicy(),
        )


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_a_change_in_only_a_role_field_is_an_update_and_the_same_is_not(
    backend: Backend,
) -> None:
    assert _persist_ada(backend).changed == (0,)
    assert _persist_ada(backend).changed == ()
    contacts = ("info@acme.com", "hello@acme.com")
    assert _persist_ada(backend, role_contact_emails=contacts).changed == (0,)
    (loaded,) = _leads(backend).values()
    assert loaded.lead.role_contact_emails == ("hello@acme.com", "info@acme.com")
    assert _persist_ada(backend, role_contact_emails=contacts).changed == ()
    flagged = _persist_ada(
        backend, role_contact_emails=contacts, email_is_role_address=True
    )
    assert flagged.changed == (0,)
    (loaded,) = _leads(backend).values()
    assert loaded.lead.email_is_role_address is True
    with Session(backend.engine) as s:
        assert s.scalar(sa.select(sa.func.count()).select_from(m.LeadIdentity)) == 1


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_a_row_from_before_0009_with_no_role_contact_is_not_a_change(
    backend: Backend,
) -> None:
    """NULL contacts (projected before 0009) and none (``[]``) are the same content:
    re-projecting such a lead fills ``[]`` in but is no update and logs nothing."""
    assert _persist_ada(backend).changed == (0,)
    with Session(backend.engine) as s, s.begin():
        s.execute(sa.update(m.CanonicalLeadRow).values(role_contact_emails=None))
    assert _persist_ada(backend).changed == ()
    (row,) = _rows(backend).values()
    assert row.role_contact_emails == []
    (loaded,) = _leads(backend).values()
    assert loaded.lead.role_contact_emails == ()


# Verifies: specs/lead-source-adapters/requirements.md#9.5
def test_migration_0009_adds_the_role_fields_and_reverses(blank: Backend) -> None:
    _alembic(blank, "upgrade", "0008")
    identity = uuid.uuid4()
    with blank.engine.begin() as conn:
        conn.execute(sa.insert(m.LeadIdentity).values(id=identity, created_at=NOW))
        conn.execute(
            sa.insert(CANONICAL_LEAD_BEFORE_0009).values(
                id=uuid.uuid4(),
                lead_identity_id=identity,
                full_name="Ada Lovelace",
                opt_out=False,
                suppressed=False,
                contributing_sources=["alpha"],
                computed_at=NOW,
                projection_version=1,
            )
        )

    _alembic(blank, "upgrade", "head")
    (row,) = _rows(blank).values()
    assert (row.email_is_role_address, row.role_contact_emails) == (False, None)

    _alembic(blank, "downgrade", "0008")
    columns = {
        c["name"] for c in sa.inspect(blank.engine).get_columns("canonical_lead")
    }
    assert not columns & {"email_is_role_address", "role_contact_emails"}
    _alembic(blank, "upgrade", "head")
