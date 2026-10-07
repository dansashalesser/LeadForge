"""No provider API key reaches a log line, the run report or the config snapshot.

Follow-up fu2 (2026-10-06): the redaction tests planted only the Match Key secret and
an email. Here every provider credential named in ``.env.example`` carries a sentinel
value, and the run goes through the real ``ingest`` command over every registered
adapter, each live, so each sentinel is really sent (header or query parameter, as the
adapter does it). Two provider behaviours are scripted: the shipped fixture answers
(the happy path) and an HTTP 401 that echoes the request back (the error path, where a
careless message would carry the key). Sockets stay blocked: no call leaves the
process.
"""

import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import ClassVar

import pytest
import structlog
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from leadforge.lead_ingestion import cli
from leadforge.lead_ingestion.base_source import (
    BaseLeadSource,
    Capability,
    ChargeUnit,
    CostClass,
    Endpoint,
    LeadContribution,
    RateBucket,
    RawBatch,
    SourceRequest,
)
from leadforge.lead_ingestion.database import create_store_engine
from leadforge.lead_ingestion.ingest_runner import IngestionOutcome, run_ingestion
from leadforge.lead_ingestion.match_key_digest import MATCH_KEY_SECRET_ENV
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.registry import SourceRegistry, SourceSettings
from leadforge.lead_ingestion.run_report import build_run_report, render_run_report
from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.tests.adapters.test_synthetic_zero_sockets import (
    make_lead,
)
from leadforge.lead_ingestion.tests.socket_guard import SocketGuard, guard_for_mode
from leadforge.lead_ingestion.transport import (
    FixtureTransport,
    Transport,
    TransportResponse,
)

REPO_ROOT = Path(__file__).resolve().parents[5]
PROFILE = REPO_ROOT / "config" / "target_profile.yaml"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
DISCOVERED = SourceRegistry.discover()

# The provider credentials of .env.example, each with a sentinel long enough for the
# redactor (short values are ignored by design) and unlike any other text.
SENTINELS = {
    "APOLLO_API_KEY": "apollo-key-sentinel-" + "a1" * 20,
    "HUBSPOT_ACCESS_TOKEN": "hubspot-token-sentinel-" + "b2" * 20,
    "HUNTER_API_KEY": "hunter-key-sentinel-" + "c3" * 20,
    "SERPAPI_API_KEY": "serpapi-key-sentinel-" + "d4" * 20,
}
# Required, but a setting, not a secret: a valid value lets HubSpot run live.
SETTINGS = {"HUBSPOT_API_VERSION": "2026-03"}
FIXTURES = FixtureTransport("people", {}).fixtures_root
# Shipped answer variants used in place of the default: the default CRM contact is
# opted out, which would prune the person before the paid tier is ever asked.
VARIANTS = {"hubspot": "not_opted_out"}
# Optional plan settings at their largest documented figures, so pacing never waits.
PLANS = {"APOLLO_PLAN": "organization", "SERPAPI_HOURLY_LIMIT": "6000"}


class People(BaseLeadSource):
    """A test-only Discovery source: one person every enrichment adapter is asked about
    (an address for the verifier and the CRM, a domain for search), even when every
    provider refuses the key."""

    name: ClassVar[str] = "people"
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.SEARCH})
    rate_limit: ClassVar[Mapping[str, RateBucket]] = {}
    answerable_surfaces: ClassVar[Mapping[str, frozenset[str]]] = {}
    cost_class: ClassVar[CostClass] = CostClass.FREE
    charge_unit: ClassVar[ChargeUnit] = ChargeUnit.PER_CALL
    yields_suppression: ClassVar[bool] = False
    target_vocabulary: ClassVar[Mapping[str, object]] = {}
    endpoints: ClassVar[Mapping[str, Endpoint]] = {}
    required_env: ClassVar[tuple[str, ...]] = ()

    async def fetch_raw(self, request: SourceRequest) -> RawBatch:
        return RawBatch(source_name=self.name, payload={})

    def normalize(self, raw: RawBatch) -> list[LeadContribution]:
        return [
            make_lead(
                self.name,
                person__first_name="Ada",
                person__last_name="Lovelace",
                person__email="ada@example.com",
                person__email_status="verified",
                company__name="Example Co",
                company__domain="example.com",
            )
        ]


class Recording:
    """Wraps one adapter's transport: records what was sent, answers per ``mode``."""

    def __init__(self, source_class: type[BaseLeadSource], mode: str) -> None:
        self.mode = mode
        self.fixtures = FixtureTransport(source_class.name, source_class.endpoints)
        self.names = {
            endpoint: name for name, endpoint in source_class.endpoints.items()
        }
        variant = VARIANTS.get(source_class.name)
        self.variant = (
            None if variant is None else FIXTURES / source_class.name / variant
        )
        self.sent: list[str] = []

    async def send(
        self,
        endpoint: Endpoint,
        *,
        params: Mapping[str, object] | None,
        json_body: Mapping[str, object] | None,
        headers: Mapping[str, str],
    ) -> TransportResponse:
        request = {"params": params, "json": json_body, "headers": dict(headers)}
        self.sent.append(json.dumps(request, default=str))
        if self.mode == "unauthorized_echo":
            # A provider that echoes the whole request, key included, in its error.
            return TransportResponse(401, {}, {"message": "bad key", "echo": request})
        chosen = (
            None
            if self.variant is None
            else self.variant / f"{self.names[endpoint]}.json"
        )
        if chosen is not None and chosen.is_file():
            body = json.loads(chosen.read_text(encoding="utf-8"))
            return TransportResponse(200, {}, body)
        return await self.fixtures.send(
            endpoint, params=params, json_body=json_body, headers=headers
        )


@pytest.fixture
def environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """Every provider key set to its sentinel, sockets blocked, a store of its own."""
    for name in DISCOVERED.names():
        for variable in DISCOVERED.source_class(name).optional_env:
            monkeypatch.delenv(variable, raising=False)
    for variable in ("LEADFORGE_MODE", "LEADFORGE_ENV_FILE", MATCH_KEY_SECRET_ENV):
        monkeypatch.delenv(variable, raising=False)
    for variable, value in {**SENTINELS, **SETTINGS, **PLANS}.items():
        monkeypatch.setenv(variable, value)
    monkeypatch.chdir(tmp_path)  # no .env, no config/ in reach
    database = tmp_path / "store.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")
    guard: SocketGuard = guard_for_mode(DataMode.SYNTHETIC)  # refuses every socket
    guard.install(monkeypatch)
    yield database
    guard.assert_clean()


@pytest.fixture
def restore_structlog() -> Iterator[None]:
    yield
    structlog.reset_defaults()


def _recorded_registry(mode: str) -> tuple[SourceRegistry, list[Recording]]:
    """Every registered adapter, its live transport replaced by a recording one."""
    recordings: list[Recording] = []

    def recorded(source_class: type[BaseLeadSource]) -> type[BaseLeadSource]:
        recording = Recording(source_class, mode)
        recordings.append(recording)

        def build_transport(
            cls: type[BaseLeadSource],
            mode: DataMode,
            *,
            fixtures_root: Path | None = None,
        ) -> Transport:
            return recording

        return type(
            source_class.__name__,
            (source_class,),
            {"build_transport": classmethod(build_transport)},
        )

    registry = SourceRegistry(
        [People]
        + [recorded(DISCOVERED.source_class(name)) for name in DISCOVERED.names()],
        {People.name: SourceSettings(mode=DataMode.SYNTHETIC)},
    )
    return registry, recordings


# Verifies: specs/lead-source-adapters/requirements.md#10.5
def test_every_provider_credential_in_env_example_has_a_sentinel() -> None:
    declared = {
        line.split("=", 1)[0]
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    }
    required = {
        variable
        for name in DISCOVERED.names()
        for variable in DISCOVERED.source_class(name).required_env
    }
    assert required <= declared
    assert set(SENTINELS) == required - set(SETTINGS)


# Verifies: specs/lead-source-adapters/requirements.md#10.5
# Verifies: specs/lead-source-adapters/requirements.md#21.3
@pytest.mark.parametrize("mode", ["fixture_answers", "unauthorized_echo"])
def test_no_provider_key_reaches_a_log_the_report_or_the_config_snapshot(
    mode: str,
    environment: Path,
    monkeypatch: pytest.MonkeyPatch,
    restore_structlog: None,
) -> None:
    registry, recordings = _recorded_registry(mode)
    outcomes: list[IngestionOutcome] = []

    async def with_registry() -> IngestionOutcome:
        outcome = await run_ingestion(registry=registry, target_profile_path=PROFILE)
        outcomes.append(outcome)
        return outcome

    monkeypatch.setattr(cli, "run_ingestion", with_registry)

    result = CliRunner().invoke(cli.app, ["ingest"])

    assert result.exception is None or isinstance(result.exception, SystemExit), repr(
        result.exception
    )
    assert result.exit_code in (0, 1), result.output  # a run, not a config error
    [outcome] = outcomes
    sent = "\n".join(text for r in recordings for text in r.sent)
    for variable, sentinel in SENTINELS.items():
        assert sentinel in sent, (
            f"{variable} was never sent: the check would be vacuous"
        )
    lines = [ln for ln in result.stderr.splitlines() if ln.startswith("{")]
    assert lines, "the configured log chain wrote nothing"

    engine = create_store_engine(f"sqlite:///{environment}")
    try:
        with Session(engine) as session:
            run = session.get_one(m.IngestionRun, outcome.run_id)
            snapshot = json.dumps(run.config_snapshot, default=str)
            report = render_run_report(build_run_report(session, outcome.run_id))
        with engine.connect() as connection:
            stored = {
                table.name: json.dumps(
                    [list(row) for row in connection.execute(table.select())],
                    default=str,
                )
                for table in m.Base.metadata.sorted_tables
                if table.name != m.RawResponse.__tablename__  # raw is kept verbatim
            }
    finally:
        engine.dispose()

    for variable, sentinel in SENTINELS.items():
        for where, text in (
            ("stdout (summary and report)", result.stdout),
            ("stderr (logs)", result.stderr),
            ("run report", report + outcome.report_text),
            ("config snapshot", snapshot),
        ):
            assert sentinel not in text, f"{variable} leaked into the {where}"
        leaked = [name for name, rows in stored.items() if sentinel in rows]
        assert leaked == [], f"{variable} leaked into {leaked}"
