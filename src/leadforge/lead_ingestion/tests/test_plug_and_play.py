"""Plug-and-play proof: a runtime-registered throwaway source runs end to end (3.2).

The throwaway class is defined inside this test module, outside the adapter package,
and handed to ``SourceRegistry.register``; nothing in the registry, models, or store
is edited for it. The run composes the existing seams (active list, fetch, checked
normalization, persistence, read-back) in synthetic mode with sockets forbidden.
There is no orchestrator yet (task 11), so this is the single place the path is wired.
"""

import socket
import uuid
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

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
from leadforge.lead_ingestion.errors import DuplicateSourceNameError
from leadforge.lead_ingestion.models import (
    AbsenceKind,
    ConfidenceOrigin,
    DataMode,
    FieldProvenance,
    SourceAbsence,
)
from leadforge.lead_ingestion.registry import SourceRegistry
from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.store.contributions import (
    read_contribution,
    write_contribution,
)
from leadforge.lead_ingestion.store.migrate import upgrade_to_head
from leadforge.lead_ingestion.store.raw_responses import (
    RawResponseRepository,
    RetentionPolicy,
)
from leadforge.lead_ingestion.store.transactions import StoreWriter

T0 = datetime(2026, 10, 5, 12, 0, 0, tzinfo=UTC)


class ThrowawaySource(BaseLeadSource):
    name: ClassVar[str] = "throwaway"
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
        return RawBatch(source_name=self.name, payload={"company": "Acme"})

    def normalize(self, raw: RawBatch) -> list[LeadContribution]:
        return [
            LeadContribution(
                source_name=self.name,
                absences=(
                    SourceAbsence(
                        canonical_path="company.phone",
                        source_name=self.name,
                        kind=AbsenceKind.NOT_APPLICABLE,
                    ),
                ),
                values={"company.name": raw.payload["company"]},
                provenance=(
                    FieldProvenance(
                        canonical_path="company.name",
                        source_name=self.name,
                        data_mode=self.data_mode,
                        fetched_at=T0,
                        raw_field_path="company",
                        confidence_origin=ConfidenceOrigin.NONE,
                        untrusted=False,
                    ),
                ),
            )
        ]


class OtherThrowaway(ThrowawaySource):
    name: ClassVar[str] = "other-throwaway"


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    eng = create_store_engine(f"sqlite:///{tmp_path / 'store.db'}")
    upgrade_to_head(eng.url.render_as_string(hide_password=True))
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("a synthetic run opened a socket")

    # Connection points only: the event loop itself needs socketpair().
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket.socket, "sendto", refuse)  # unconnected UDP
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


def test_socket_guard_blocks_every_outbound_path() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        for call in (
            lambda: udp.connect(("127.0.0.1", 9)),
            lambda: udp.connect_ex(("127.0.0.1", 9)),
            lambda: udp.sendto(b"x", ("127.0.0.1", 9)),
            lambda: socket.getaddrinfo("example.com", 80),
            lambda: socket.create_connection(("127.0.0.1", 9)),
        ):
            with pytest.raises(AssertionError, match="opened a socket"):
                call()


# Verifies: specs/lead-source-adapters/requirements.md#3.2
def test_runtime_registered_source_appears_in_listing() -> None:
    registry = SourceRegistry.discover()
    before = registry.names()

    registry.register(ThrowawaySource)

    assert "throwaway" not in before
    assert "throwaway" in registry.names()
    assert registry.source_class("throwaway") is ThrowawaySource
    by_name = {d.name: d for d in registry.describe(environ={})}
    assert by_name["throwaway"].enabled is True
    assert by_name["throwaway"].resolved_mode is DataMode.SYNTHETIC
    assert by_name["throwaway"].capabilities == (Capability.SEARCH,)
    assert "throwaway" in registry.enabled_names()


# Verifies: specs/lead-source-adapters/requirements.md#3.2
def test_runtime_registration_is_per_registry_not_global() -> None:
    SourceRegistry.discover().register(ThrowawaySource)

    assert "throwaway" not in SourceRegistry.discover().names()


# Verifies: specs/lead-source-adapters/requirements.md#3.2
def test_runtime_registration_of_a_taken_name_is_rejected() -> None:
    registry = SourceRegistry([ThrowawaySource])

    class Clash(ThrowawaySource):
        name: ClassVar[str] = "Throwaway "

    with pytest.raises(DuplicateSourceNameError):
        registry.register(Clash)
    assert registry.names() == ("throwaway",)


# Verifies: specs/lead-source-adapters/requirements.md#3.2
def test_runtime_registration_rejects_non_sources_and_abstract_classes() -> None:
    registry = SourceRegistry([])

    class Incomplete(BaseLeadSource):
        name: ClassVar[str] = "incomplete"

    class Nameless(ThrowawaySource):
        name: ClassVar[str] = " "

    for bad in (object(), int, BaseLeadSource, Incomplete, Nameless):
        with pytest.raises(TypeError):
            registry.register(bad)  # type: ignore[arg-type]
    assert registry.names() == ()


# Verifies: specs/lead-source-adapters/requirements.md#3.2
async def test_runtime_registered_source_completes_a_synthetic_run(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    checked: list[str] = []
    original = ThrowawaySource.validate_absence

    def spy(self: BaseLeadSource, absence: SourceAbsence) -> SourceAbsence:
        checked.append(absence.canonical_path)
        return original(self, absence)

    monkeypatch.setattr(ThrowawaySource, "validate_absence", spy)
    registry = SourceRegistry.discover()
    registry.register(ThrowawaySource)
    registry.register(OtherThrowaway)
    built: list[BaseLeadSource] = []

    def factory(cls: type[BaseLeadSource]) -> BaseLeadSource:
        transport = cls.build_transport(DataMode.SYNTHETIC)
        built.append(cls(DataMode.SYNTHETIC, transport=transport))
        return built[-1]

    sources = [s for s in registry.active(factory) if s.name == "throwaway"]
    assert len(sources) == 1
    source = sources[0]
    assert source.data_mode is DataMode.SYNTHETIC

    writer = StoreWriter(engine)
    run_id = await writer.begin_run(status="running")
    raw = await source.fetch_raw(SourceRequest(kind="discover"))
    contributions = source.normalize_checked(raw)
    assert len(contributions) == 1
    assert checked == ["company.phone"]  # the absence went through the checked path

    def persist(session: Session) -> uuid.UUID:
        source_run = m.SourceRun(
            run_id=run_id, source_name=source.name, resolved_mode="synthetic"
        )
        session.add(source_run)
        session.flush()
        raw_id = RawResponseRepository.add(
            session,
            source_run_id=source_run.id,
            endpoint_key="discover",
            request_fingerprint="synthetic",
            payload=raw.payload,
            fetched_at=T0,
            mode=DataMode.SYNTHETIC,
            policy=RetentionPolicy(),
        )
        return write_contribution(
            session,
            contributions[0],
            source_run_id=source_run.id,
            raw_response_id=raw_id,
            data_mode=DataMode.SYNTHETIC,
            fetched_at=T0,
            lead_scope="company",
        )

    contribution_id = await writer.write_batch(persist)

    with Session(engine) as session:
        stored = read_contribution(session, contribution_id)
    assert stored.source_name == "throwaway"
    assert stored.data_mode is DataMode.SYNTHETIC
    assert stored.values == {"company.name": "Acme"}
