"""One REAL adapter failing alone through the real run (follow-up 2026-10-06; 6.1, 6.5).

The other failure-isolation tests inject failures through scripted stand-in sources.
Here the failing source is the shipped Google Search adapter itself: only its transport
is replaced, by one that answers every provider call with HTTP 500, so the adapter's
own request building, error classification and the orchestrator's ledger are what
record the failure. Every other shipped adapter runs over its fixtures as usual.

Google is the adapter chosen because a zero-credential run really reaches its provider
(Enrichment asks about the company domain Apollo's match yields); HubSpot and Hunter
make no provider call in that run (GAP (c), test_zero_credential_run_adapters.py), so a
500 from them would never be observed. Status vocabulary: the run is ``completed`` with
exit code 0 (at least one source succeeded, 6.4/6.5); the source's own row carries the
failure class and its call counts.
"""

from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.adapters.google_search import GoogleSearchSource
from leadforge.lead_ingestion.base_source import Endpoint
from leadforge.lead_ingestion.database import create_store_engine
from leadforge.lead_ingestion.ingest_runner import run_ingestion
from leadforge.lead_ingestion.match_key_digest import MATCH_KEY_SECRET_ENV
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.orchestrator import Phase, SourceStatus
from leadforge.lead_ingestion.registry import SourceRegistry
from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.tests.socket_guard import SocketGuard, guard_for_mode
from leadforge.lead_ingestion.transport import Transport, TransportResponse

REPO_ROOT = Path(__file__).resolve().parents[5]
PROFILE = REPO_ROOT / "config" / "target_profile.yaml"
DISCOVERED = SourceRegistry.discover()
FAILING = GoogleSearchSource.name


class ServerErrors:
    """A transport whose provider answers every call with HTTP 500."""

    def __init__(self) -> None:
        self.calls = 0

    async def send(
        self,
        endpoint: Endpoint,
        *,
        params: Mapping[str, object] | None,
        json_body: Mapping[str, object] | None,
        headers: Mapping[str, str],
    ) -> TransportResponse:
        self.calls += 1
        return TransportResponse(500, {}, {"error": "Internal Server Error"})


@pytest.fixture
def database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """An empty environment (every source synthetic), sockets blocked, own store."""
    for name in DISCOVERED.names():
        for variable in DISCOVERED.source_class(name).required_env:
            monkeypatch.delenv(variable, raising=False)
    for variable in ("LEADFORGE_MODE", "LEADFORGE_ENV_FILE", MATCH_KEY_SECRET_ENV):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "store.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    guard: SocketGuard = guard_for_mode(DataMode.SYNTHETIC)
    guard.install(monkeypatch)
    yield path
    guard.assert_clean()


def _registry_with_failing_google(transport: ServerErrors) -> SourceRegistry:
    class FailingGoogle(GoogleSearchSource):
        @classmethod
        def build_transport(
            cls, mode: DataMode, *, fixtures_root: Path | None = None
        ) -> Transport:
            return transport

    return SourceRegistry(
        [
            FailingGoogle if name == FAILING else DISCOVERED.source_class(name)
            for name in DISCOVERED.names()
        ]
    )


# Verifies: specs/lead-source-adapters/requirements.md#6.1
# Verifies: specs/lead-source-adapters/requirements.md#6.5
# Verifies: specs/lead-source-adapters/requirements.md#21.2
async def test_a_real_adapter_failing_alone_leaves_every_other_source_complete(
    database: Path,
) -> None:
    transport = ServerErrors()

    outcome = await run_ingestion(
        registry=_registry_with_failing_google(transport),
        target_profile_path=PROFILE,
    )

    assert transport.calls >= 1  # the real adapter really reached its provider
    assert outcome.exit.exit_code == 0
    latest = {r.source_name: r.outcome for r in outcome.results}
    failing = latest[FAILING]
    assert failing.status is SourceStatus.TRANSIENT
    assert failing.failed >= 1
    enrichment = [
        r
        for r in outcome.results
        if (r.source_name, r.phase) == (FAILING, Phase.ENRICHMENT)
    ]
    assert enrichment
    assert enrichment[0].batch is None  # nothing was taken from it
    for name in set(DISCOVERED.names()) - {FAILING}:
        assert latest[name].status is SourceStatus.OK, name
        assert latest[name].failed == 0, name

    engine = create_store_engine(f"sqlite:///{database}")
    try:
        with Session(engine) as session:
            run = session.get_one(m.IngestionRun, outcome.run_id)
            rows = {
                r.source_name: r
                for r in session.scalars(
                    sa.select(m.SourceRun).where(m.SourceRun.run_id == outcome.run_id)
                )
            }
            leads = session.scalar(sa.select(sa.func.count(m.CanonicalLeadRow.id)))
    finally:
        engine.dispose()
    assert (run.status, run.exit_code, run.failure_reason) == ("completed", 0, None)
    row = rows[FAILING]
    assert row.failure_class == SourceStatus.TRANSIENT.value
    assert (row.attempted, row.succeeded, row.failed) == (
        failing.attempted,
        failing.succeeded,
        failing.failed,
    )
    assert {rows[n].failure_class for n in rows if n != FAILING} == {None}
    assert (leads or 0) >= 1  # the other sources' Leads were merged and stored

    summary = outcome.exit.summary.splitlines()
    assert (
        f"{FAILING}: transient attempted={failing.attempted} "
        f"succeeded={failing.succeeded} failed={failing.failed}"
    ) in summary
    report = outcome.report_text.splitlines()
    at = report.index(next(ln for ln in report if ln.startswith(f"{FAILING}: mode=")))
    assert report[at + 1].startswith("  failure=transient ")
    assert report[at + 2] == (
        f"  calls: attempted={failing.attempted} "
        f"succeeded={failing.succeeded} failed={failing.failed}"
    )
