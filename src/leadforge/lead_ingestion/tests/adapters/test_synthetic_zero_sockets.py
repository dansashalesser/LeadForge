"""A synthetic run opens zero sockets (task 19.2, Requirements 4.1 and 11.5).

One consolidated guard over the real ``IngestionOrchestrator`` run across every
registered source in synthetic mode: mode resolution, the orchestrator, every
adapter over its fixtures (``FixtureTransport``), the merge, a run record on a local
SQLite engine, and the run report. The guard (``tests/socket_guard.py``) covers every
Python-level way to open a connection or resolve a name; what it allows is stated
there (AF_UNIX and the file I/O of SQLite). A violation is recorded as well as raised,
so a source that swallows the error still fails the run.

Out of scope, and said so: a Postgres engine is a connection, so this run uses the
local SQLite default; native resolvers or sockets reached below Python (a C extension
calling ``connect(2)``) are not intercepted.
"""

import asyncio
import builtins
import contextlib
import io
import os
import socket
import sqlite3
import threading
from collections.abc import Awaitable, Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.adapters.apollo import ApolloSource
from leadforge.lead_ingestion.adapters.google_search import GoogleSearchSource
from leadforge.lead_ingestion.adapters.hubspot import HubSpotSource
from leadforge.lead_ingestion.adapters.hunter import HunterSource
from leadforge.lead_ingestion.base_source import (
    BaseLeadSource,
    Capability,
    ChargeUnit,
    CostClass,
    Endpoint,
    EnrichmentRequest,
    LeadContribution,
    RateBucket,
    RawBatch,
    SourceRequest,
)
from leadforge.lead_ingestion.clustering import cluster_contributions
from leadforge.lead_ingestion.companies import CompanyCluster
from leadforge.lead_ingestion.database import create_store_engine
from leadforge.lead_ingestion.mode_resolution import resolve_data_mode
from leadforge.lead_ingestion.models import ConfidenceOrigin, DataMode, FieldProvenance
from leadforge.lead_ingestion.orchestrator import (
    IngestionOrchestrator,
    SourceResult,
    SourceStatus,
)
from leadforge.lead_ingestion.over_merge import detect_over_merges
from leadforge.lead_ingestion.pacing import SourcePacing
from leadforge.lead_ingestion.primary_domain import PrimaryDomain
from leadforge.lead_ingestion.projection import project_lead
from leadforge.lead_ingestion.registry import SourceRegistry, SourceSettings
from leadforge.lead_ingestion.run_recorder import StoreRunRecorder
from leadforge.lead_ingestion.run_report import build_run_report, render_run_report
from leadforge.lead_ingestion.store.migrate import upgrade_to_head
from leadforge.lead_ingestion.store.transactions import StoreWriter
from leadforge.lead_ingestion.tests.socket_guard import (
    CHANNELS,
    SocketGuard,
    SocketGuardError,
    guard_for_mode,
    inet_socket,
)
from leadforge.lead_ingestion.tie_resolution import (
    TieResolutionRecord,
    TieResolver,
    TieSource,
    resolve_primary_domain,
)
from leadforge.lead_ingestion.transport import FixtureTransport, RestTransport

_REGISTRY = SourceRegistry.discover()
PROVIDERS = frozenset({"apollo", "hubspot", "google_search", "hunter"})
CHANNEL_NAMES = sorted(CHANNELS)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    eng = create_store_engine(f"sqlite:///{tmp_path / 'store.db'}")
    yield eng
    eng.dispose()


@pytest.fixture
def clean_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    """No credential or mode override, no ``.env`` in reach, any read of one logged."""
    for name in _REGISTRY.names():
        for variable in _REGISTRY.source_class(name).required_env:
            monkeypatch.delenv(variable, raising=False)
    monkeypatch.delenv("LEADFORGE_MODE", raising=False)
    monkeypatch.chdir(tmp_path)  # a directory with no .env
    dotenv_reads: list[str] = []
    real_open = builtins.open
    real_io_open = io.open

    def watch(opener: Callable[..., Any]) -> Callable[..., Any]:
        def opened(file: Any, *args: Any, **kwargs: Any) -> Any:
            if isinstance(file, str | os.PathLike) and Path(
                os.fspath(file)
            ).name.startswith(".env"):
                dotenv_reads.append(str(file))
            return opener(file, *args, **kwargs)

        return opened

    monkeypatch.setattr(builtins, "open", watch(real_open))
    monkeypatch.setattr(io, "open", watch(real_io_open))
    return dotenv_reads


# ------------------------------------------------------------ the guard is real


# Verifies: specs/lead-source-adapters/requirements.md#11.5
@pytest.mark.parametrize("channel", CHANNEL_NAMES)
async def test_the_guard_refuses_and_names_every_channel(
    channel: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    guard = guard_for_mode(DataMode.SYNTHETIC)
    guard.install(monkeypatch)

    with pytest.raises(SocketGuardError, match=channel):
        await CHANNELS[channel]()

    assert guard.violations
    assert guard.violations[0].startswith(f"{channel}(")
    with pytest.raises(AssertionError, match="synthetic run opened a connection"):
        guard.assert_clean()


# Verifies: specs/lead-source-adapters/requirements.md#11.5
async def test_the_guard_allows_local_ipc_and_sqlite_and_nothing_else_here(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    guard = guard_for_mode(DataMode.SYNTHETIC)
    guard.install(monkeypatch)

    left, right = socket.socketpair()  # what the event loop itself needs
    with left, right:
        left.sendall(b"x")
        assert right.recv(1) == b"x"
    with (
        socket.socket(socket.AF_UNIX) as unix,
        pytest.raises(OSError, match="No such file"),
    ):
        unix.connect(str(tmp_path / "absent.sock"))
    with sqlite3.connect(tmp_path / "local.db") as db:
        db.execute("create table t (x integer)")

    assert guard.violations == []
    guard.assert_clean()


# Verifies: specs/lead-source-adapters/requirements.md#11.5
async def test_the_guard_does_not_apply_to_a_live_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard = guard_for_mode(DataMode.LIVE)
    guard.install(monkeypatch)

    for channel in CHANNEL_NAMES:
        await CHANNELS[channel]()  # attempted, recorded, and stopped short of the wire

    assert guard.violations == []
    assert [a.split("(")[0] for a in guard.attempts] == CHANNEL_NAMES
    guard.assert_clean()


# Verifies: specs/lead-source-adapters/requirements.md#4.1
def test_the_environment_fixture_is_clean_and_watches_dotenv_reads(
    clean_environment: list[str], tmp_path: Path
) -> None:
    for name in _REGISTRY.names():
        assert not [
            v for v in _REGISTRY.source_class(name).required_env if v in os.environ
        ]
    assert not (Path.cwd() / ".env").exists()

    (tmp_path / ".env").write_text("X=1\n")
    (tmp_path / ".env").read_text()

    assert clean_environment == [str(tmp_path / ".env")] * 2


# ------------------------------------------------------------------ the real run


@dataclass
class Pipeline:
    results: tuple[SourceResult, ...]
    built: dict[str, BaseLeadSource]
    report_text: str
    merged_leads: int
    suspects: int


def build_synthetic(
    source_class: type[BaseLeadSource], mode: DataMode, pacing: SourcePacing | None
) -> BaseLeadSource:
    """A source over its own fixtures, the way the composition root must build one."""
    transport = source_class.build_transport(mode)
    if source_class is ApolloSource:
        return ApolloSource(
            mode, transport=transport, pacing=pacing, vocabulary={"t": ["datastax"]}
        )
    if source_class is HubSpotSource:
        return HubSpotSource(mode, transport=transport, pacing=pacing)
    if source_class is HunterSource:
        return HunterSource(mode, transport=transport, pacing=pacing)
    if source_class is GoogleSearchSource:
        return GoogleSearchSource(
            mode, transport=transport, queries=("example platform jobs",), pacing=pacing
        )
    raise AssertionError(f"no synthetic builder for {source_class.name!r}: add one")


async def run_pipeline(
    engine: Engine,
    registry: SourceRegistry,
    build: Callable[
        [type[BaseLeadSource], DataMode, SourcePacing | None], BaseLeadSource
    ] = build_synthetic,
) -> Pipeline:
    upgrade_to_head(engine.url.render_as_string(hide_password=False))
    built: dict[str, BaseLeadSource] = {}

    def resolve(source_class: type[BaseLeadSource], settings: SourceSettings) -> Any:
        return resolve_data_mode(source_class, settings)

    def record(
        source_class: type[BaseLeadSource], mode: DataMode, pacing: SourcePacing | None
    ) -> BaseLeadSource:
        built[source_class.name] = build(source_class, mode, pacing)
        return built[source_class.name]

    orchestrator = IngestionOrchestrator(
        registry,
        resolve_mode=resolve,
        build_source=record,
        max_concurrent_sources=4,
        run_timeout_s=60,
        run_recorder=StoreRunRecorder(StoreWriter(engine)),
    )
    results = await orchestrator.run(SourceRequest(kind="discovery"))
    contributions: list[LeadContribution] = [
        c for r in results for c in (r.contributions or ())
    ]
    ranks = {name: i for i, name in enumerate(registry.names())}
    clusters = cluster_contributions(contributions)
    merged = [project_lead(c, ranks) for c in clusters]
    suspects = detect_over_merges(clusters)
    with Session(engine) as session:
        report_text = render_run_report(build_run_report(session))
    return Pipeline(results, built, report_text, len(merged), len(suspects))


def assert_pure_synthetic(
    pipeline: Pipeline, guard: SocketGuard, expected: frozenset[str]
) -> None:
    """The run was synthetic by construction as well as by observation."""
    guard.assert_clean()
    assert {r.source_name for r in pipeline.results} == expected
    for result in pipeline.results:
        assert result.resolved_mode is DataMode.SYNTHETIC, result.source_name
        assert result.outcome.status is SourceStatus.OK, result.source_name
        assert result.throttle is None  # synthetic has no pacing (7.5)
        transport = pipeline.built[result.source_name].transport
        assert isinstance(transport, FixtureTransport), result.source_name
        assert not isinstance(transport, RestTransport)


# Verifies: specs/lead-source-adapters/requirements.md#4.1
# Verifies: specs/lead-source-adapters/requirements.md#11.5
async def test_a_synthetic_run_over_every_registered_source_opens_zero_sockets(
    engine: Engine,
    clean_environment: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert set(_REGISTRY.names()) >= PROVIDERS
    guard = guard_for_mode(DataMode.SYNTHETIC)
    guard.install(monkeypatch)

    pipeline = await run_pipeline(engine, SourceRegistry.discover())

    assert_pure_synthetic(pipeline, guard, frozenset(_REGISTRY.names()))
    assert clean_environment == []  # no .env read
    contributions = [c for r in pipeline.results for c in r.contributions or ()]
    assert contributions  # the run produced data, not an empty pass
    assert pipeline.merged_leads >= 1
    for name in PROVIDERS:
        assert name in pipeline.report_text
    assert "synthetic" in pipeline.report_text


# ------------------------------------------------------------ mutation proofs


def probing_source(channel: str, source_name: str = "probing") -> type[BaseLeadSource]:
    """A throwaway adapter whose fetch opens a connection through ``channel``."""

    class Probing(BaseLeadSource):
        name: ClassVar[str] = source_name
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
            await CHANNELS[channel]()
            return RawBatch(source_name=self.name, payload={})

        def normalize(self, raw: RawBatch) -> list[LeadContribution]:
            return []

    return Probing


def build_probing(
    source_class: type[BaseLeadSource], mode: DataMode, pacing: SourcePacing | None
) -> BaseLeadSource:
    return source_class(mode)


# Verifies: specs/lead-source-adapters/requirements.md#11.5
@pytest.mark.parametrize("channel", CHANNEL_NAMES)
async def test_a_synthetic_run_with_a_socket_opening_adapter_fails_the_guard(
    channel: str,
    engine: Engine,
    clean_environment: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard = guard_for_mode(DataMode.SYNTHETIC)
    guard.install(monkeypatch)
    registry = SourceRegistry(
        [probing_source(channel)], {"probing": SourceSettings(mode=DataMode.SYNTHETIC)}
    )

    with pytest.raises(ExceptionGroup) as raised:
        await run_pipeline(engine, registry, build_probing)

    assert raised.group_contains(SocketGuardError)
    assert guard.violations, f"{channel} went unnoticed"
    assert guard.violations[0].startswith(f"{channel}(")
    with pytest.raises(AssertionError, match=f"first violation: {channel}"):
        guard.assert_clean()


# Verifies: specs/lead-source-adapters/requirements.md#11.5
@pytest.mark.parametrize("channel", CHANNEL_NAMES)
async def test_a_live_run_of_the_same_adapter_is_left_to_attempt(
    channel: str,
    engine: Engine,
    clean_environment: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard = guard_for_mode(DataMode.LIVE)
    guard.install(monkeypatch)
    registry = SourceRegistry(
        [probing_source(channel)], {"probing": SourceSettings(mode=DataMode.LIVE)}
    )

    await run_pipeline(engine, registry, build_probing)

    assert [a.split("(")[0] for a in guard.attempts] == [channel]
    assert guard.violations == []


# Verifies: specs/lead-source-adapters/requirements.md#11.5
async def test_a_source_resolved_synthetic_but_built_with_a_live_transport_fails(
    engine: Engine,
    clean_environment: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard = guard_for_mode(DataMode.SYNTHETIC)
    guard.install(monkeypatch)

    def miswired(
        source_class: type[BaseLeadSource], mode: DataMode, pacing: SourcePacing | None
    ) -> BaseLeadSource:
        if source_class is ApolloSource:
            live = source_class.build_transport(DataMode.LIVE)  # the mis-build
            return ApolloSource(
                mode,
                transport=live,
                environ={"APOLLO_API_KEY": "x"},
                vocabulary={"t": ["datastax"]},
            )
        return build_synthetic(source_class, mode, pacing)

    with pytest.raises(ExceptionGroup) as raised:
        await run_pipeline(engine, SourceRegistry.discover(), miswired)

    assert raised.group_contains(SocketGuardError)
    assert any(v.startswith("httpx.AsyncClient(") for v in guard.violations)
    with pytest.raises(AssertionError, match=r"first violation: httpx\.AsyncClient"):
        guard.assert_clean()


# ----------------------------------------------- the guard on aliases and threads


def _listen() -> None:
    with socket.create_server(("127.0.0.1", 0)):
        pass


def _smtp() -> None:
    import smtplib

    smtplib.SMTP("example.invalid", 25)


def _ftp() -> None:
    import ftplib

    ftplib.FTP("example.invalid")


def _https() -> None:
    import http.client

    http.client.HTTPSConnection("example.invalid").connect()


def _requests() -> None:
    requests = pytest.importorskip("requests")
    requests.get("https://example.invalid/", timeout=1)


def _spawn_process() -> None:
    import multiprocessing

    multiprocessing.get_context("fork").Process(target=int).start()


def _run_in_thread(action: Callable[[], object]) -> Callable[[], None]:
    def run() -> None:
        failures: list[BaseException] = []

        def target() -> None:
            try:
                action()
            except BaseException as error:  # noqa: BLE001  # reported to the caller thread below
                failures.append(error)

        worker = threading.Thread(target=target)
        worker.start()
        worker.join()
        if failures:
            raise failures[0]

    return run


ALIASES: Mapping[str, Callable[[], object]] = {
    "listening inet socket": _listen,
    "smtplib": _smtp,
    "ftplib": _ftp,
    "http.client.HTTPSConnection": _https,
    "requests": _requests,
    "os.spawnv": lambda: os.spawnv(os.P_WAIT, "/bin/true", ["true"]),
    "os.popen": lambda: os.popen("true").close(),
    "subprocess.run": lambda: __import__("subprocess").run(["true"], check=False),
    "multiprocessing fork": _spawn_process,
    "socket.getaddrinfo in a thread": _run_in_thread(
        lambda: socket.getaddrinfo("example.invalid", 80)
    ),
    "socket.create_connection in a thread": _run_in_thread(
        lambda: socket.create_connection(("example.invalid", 80))
    ),
}


# Verifies: specs/lead-source-adapters/requirements.md#11.5
@pytest.mark.parametrize("alias", sorted(ALIASES))
async def test_the_guard_also_stops_the_wrappers_threads_and_processes(
    alias: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    guard = guard_for_mode(DataMode.SYNTHETIC)
    guard.install(monkeypatch)

    with pytest.raises(SocketGuardError):
        ALIASES[alias]()

    assert guard.violations


# Verifies: specs/lead-source-adapters/requirements.md#11.5
@pytest.mark.parametrize("via", ["to_thread", "executor", "loop.getaddrinfo"])
async def test_a_lookup_from_another_thread_of_the_event_loop_is_caught(
    via: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    guard = guard_for_mode(DataMode.SYNTHETIC)
    guard.install(monkeypatch)
    loop = asyncio.get_running_loop()
    lookups: Mapping[str, Callable[[], Awaitable[object]]] = {
        "to_thread": lambda: asyncio.to_thread(
            socket.getaddrinfo, "example.invalid", 80
        ),
        "executor": lambda: loop.run_in_executor(
            None, socket.getaddrinfo, "example.invalid", 80
        ),
        "loop.getaddrinfo": lambda: loop.getaddrinfo("example.invalid", 80),
    }

    with pytest.raises(SocketGuardError):
        await lookups[via]()

    assert guard.violations


# Verifies: specs/lead-source-adapters/requirements.md#11.5
async def test_a_wrapped_descriptor_and_a_unix_path_are_not_outbound(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    guard = guard_for_mode(DataMode.SYNTHETIC)
    guard.install(monkeypatch)

    left, right = socket.socketpair()
    with left, right:
        rewrapped = socket.fromfd(left.fileno(), socket.AF_UNIX, socket.SOCK_STREAM)
        rewrapped.close()
    with pytest.raises(FileNotFoundError):
        await asyncio.open_unix_connection(str(tmp_path / "absent.sock"))
    with pytest.raises(FileNotFoundError):
        await asyncio.get_running_loop().create_unix_connection(
            asyncio.Protocol, str(tmp_path / "absent.sock")
        )

    assert guard.violations == []


# Verifies: specs/lead-source-adapters/requirements.md#11.5
async def test_a_connect_on_a_descriptor_wrapped_after_the_fact_is_still_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard = guard_for_mode(DataMode.SYNTHETIC)
    guard.install(monkeypatch)

    with (
        contextlib.closing(inet_socket()) as inet,
        contextlib.closing(
            socket.fromfd(inet.fileno(), socket.AF_INET, socket.SOCK_DGRAM)
        ) as again,
        pytest.raises(SocketGuardError, match=r"socket\.connect"),
    ):
        again.connect(("192.0.2.1", 9))


# -------------------------------------------------------- the guard comes off


def loopback_round_trip() -> bytes:
    """A real loopback connection: it works only when no guard is left behind."""
    with socket.create_server(("127.0.0.1", 0)) as server:
        host, port = server.getsockname()[:2]
        with socket.create_connection((host, port), timeout=2) as client:
            peer, _ = server.accept()
            with peer:
                client.sendall(b"ok")
                return peer.recv(2)


def patched_points() -> dict[str, object]:
    import _socket
    import ssl
    import subprocess

    return {
        "socket.__init__": socket.socket.__init__,
        "socket.connect": socket.socket.connect,
        "socket.create_connection": socket.create_connection,
        "socket.getaddrinfo": socket.getaddrinfo,
        "_socket.socket": _socket.socket,
        "_socket.getaddrinfo": _socket.getaddrinfo,
        "ssl.wrap_socket": ssl.SSLContext.wrap_socket,
        "loop.create_connection": asyncio.BaseEventLoop.create_connection,
        "subprocess.Popen": subprocess.Popen.__init__,
        "os.fork": os.fork,
    }


# Verifies: specs/lead-source-adapters/requirements.md#11.5
async def test_the_guard_comes_off_cleanly_even_nested_or_after_an_error() -> None:
    before = patched_points()

    with pytest.MonkeyPatch.context() as outer_patch:
        outer = guard_for_mode(DataMode.SYNTHETIC)
        outer.install(outer_patch)
        with pytest.MonkeyPatch.context() as inner_patch:
            inner = guard_for_mode(DataMode.SYNTHETIC)
            inner.install(inner_patch)
            with pytest.raises(SocketGuardError):
                socket.getaddrinfo("example.invalid", 80)
        assert patched_points() != before  # the outer guard is still on
        with pytest.raises(SocketGuardError):
            socket.getaddrinfo("example.invalid", 80)
    assert patched_points() == before

    def fail_inside_the_block() -> None:
        with pytest.MonkeyPatch.context() as failing_patch:
            guard_for_mode(DataMode.SYNTHETIC).install(failing_patch)
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        fail_inside_the_block()
    assert patched_points() == before

    assert loopback_round_trip() == b"ok"
    assert len(inner.violations) == 1  # the innermost guard raised first
    assert len(outer.violations) == 1  # only the call made after the inner one left


# Verifies: specs/lead-source-adapters/requirements.md#11.5
async def test_a_legitimate_loopback_socket_works_after_the_guard_tests() -> None:
    """Order independence: this module's guard tests leave nothing patched."""
    assert loopback_round_trip() == b"ok"


# ------------------------------------------------ a swallowed violation still fails


def swallowing_source(channel: str) -> type[BaseLeadSource]:
    base = probing_source(channel, "swallower")

    class Swallowing(base):  # type: ignore[valid-type, misc]
        async def fetch_raw(self, request: SourceRequest) -> RawBatch:
            with contextlib.suppress(Exception):  # the adapter hides the refusal
                await CHANNELS[channel]()
            return RawBatch(source_name=self.name, payload={})

    return Swallowing


# Verifies: specs/lead-source-adapters/requirements.md#11.5
@pytest.mark.parametrize("channel", ["socket.create_connection", "httpx.AsyncClient"])
async def test_a_violation_swallowed_by_the_adapter_still_fails_the_run(
    channel: str,
    engine: Engine,
    clean_environment: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard = guard_for_mode(DataMode.SYNTHETIC)
    guard.install(monkeypatch)
    registry = SourceRegistry(
        [swallowing_source(channel)],
        {"swallower": SourceSettings(mode=DataMode.SYNTHETIC)},
    )

    pipeline = await run_pipeline(engine, registry, build_probing)  # no error escapes

    assert pipeline.results[0].outcome.status is SourceStatus.OK  # it looked fine
    with pytest.raises(SocketGuardError, match=f"first violation: {channel}"):
        guard.assert_clean()


# ------------------------------------------------------- a live stop with a hole


class LeakyGuard(SocketGuard):
    """A live-mode guard whose own stop is broken: every call is let through."""

    def _gate(self, target: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> bool:
        return True


# Verifies: specs/lead-source-adapters/requirements.md#11.5
@pytest.mark.parametrize("channel", ["socket.getaddrinfo", "socket.create_connection"])
async def test_a_hole_in_a_live_guard_still_cannot_reach_the_wire(
    channel: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    leaky = LeakyGuard(enforce=False)
    leaky.install(monkeypatch)

    with pytest.raises(SocketGuardError):
        await CHANNELS[channel]()  # the backstop beneath it raises, nothing connects

    assert leaky.backstop is not None
    assert leaky.backstop.violations
    with pytest.raises(SocketGuardError):
        leaky.assert_clean()


# ------------------------------------------------- the run exercises every endpoint


def make_lead(source_name: str, **values: object) -> LeadContribution:
    """A lead from canonical path (``__`` for ``.``) to value, with its provenance."""
    paths = {k.replace("__", "."): v for k, v in values.items()}
    return LeadContribution(
        source_name=source_name,
        values=paths,
        provenance=tuple(
            FieldProvenance(
                canonical_path=path,
                source_name=source_name,
                data_mode=DataMode.SYNTHETIC,
                fetched_at=datetime(2026, 10, 5, tzinfo=UTC),
                raw_field_path=path,
                confidence_origin=ConfidenceOrigin.NONE,
                untrusted=False,
            )
            for path in paths
        ),
    )


class Seed(BaseLeadSource):
    """A discovery source that leaves the work list Hunter and HubSpot need.

    The shipped discovery fixtures name no company domain or email, so without this
    Enrichment has nothing to ask of Hunter or HubSpot and their fixtures go unread.
    (HubSpot's opt-out report then prunes the email lead, so Hunter's verifier is
    driven by its own test below.)
    """

    name: ClassVar[str] = "seed"
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
            make_lead(self.name, company__domain="alpha.example"),  # Hunter: domain
            make_lead(  # Hunter: email finder
                self.name,
                person__first_name="Ada",
                person__last_name="Lovelace",
                company__domain="beta.example",  # a company of its own, not pruned
            ),
            make_lead(self.name, person__email="ada.lovelace@example.com"),  # HubSpot
        ]


def spy_on_fixture_transports(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    served: list[str] = []
    real_send = FixtureTransport.send

    async def send(self: FixtureTransport, endpoint: Endpoint, **kwargs: Any) -> Any:
        served.append(f"{self._provider}:{endpoint.path}")
        return await real_send(self, endpoint, **kwargs)

    monkeypatch.setattr(FixtureTransport, "send", send)
    return served


# Verifies: specs/lead-source-adapters/requirements.md#4.1
# Verifies: specs/lead-source-adapters/requirements.md#11.5
async def test_every_registered_sources_fixtures_are_served_in_a_guarded_run(
    engine: Engine,
    clean_environment: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard = guard_for_mode(DataMode.SYNTHETIC)
    guard.install(monkeypatch)
    served = spy_on_fixture_transports(monkeypatch)
    real = [_REGISTRY.source_class(n) for n in _REGISTRY.names()]
    registry = SourceRegistry(
        [*real, Seed], {"seed": SourceSettings(mode=DataMode.SYNTHETIC)}
    )

    def build(
        source_class: type[BaseLeadSource], mode: DataMode, pacing: SourcePacing | None
    ) -> BaseLeadSource:
        if source_class is Seed:
            return Seed(mode, transport=Seed.build_transport(mode))
        return build_synthetic(source_class, mode, pacing)

    pipeline = await run_pipeline(engine, registry, build)

    assert_pure_synthetic(pipeline, guard, frozenset(registry.names()))
    # Every source answered from its fixtures through the fixture transport: Apollo
    # search and match, Google search, both HubSpot searches, Hunter domain search and
    # finder, and (its rerun, user decision 2026-10-07) the verifier for the address
    # Apollo's match found.
    assert set(served) == {
        "apollo:/api/v1/mixed_people/api_search",
        "apollo:/api/v1/people/match",
        "google_search:/search",
        "hubspot:/crm/objects/{version}/contacts/search",
        "hubspot:/crm/objects/{version}/deals/search",
        "hunter:/v2/domain-search",
        "hunter:/v2/email-finder",
        "hunter:/v2/email-verifier",
    }
    for result in pipeline.results:
        assert result.mode_reason.startswith(("missing credentials", "per-source"))
    assert pipeline.merged_leads >= 1
    assert clean_environment == []


# Verifies: specs/lead-source-adapters/requirements.md#4.1
async def test_the_merge_never_builds_a_tie_resolver_in_synthetic_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard = guard_for_mode(DataMode.SYNTHETIC)
    guard.install(monkeypatch)
    built: list[str] = []

    def factory() -> TieResolver:
        built.append("resolver")
        raise AssertionError("a synthetic run must not build a tie resolver")

    class Store:
        def get(self, key: str) -> TieResolutionRecord | None:
            return None

        def put(self, key: str, record: TieResolutionRecord) -> TieResolutionRecord:
            raise AssertionError("a synthetic run persists no resolution")

    outcome = resolve_primary_domain(
        CompanyCluster(company_id="c", domains=("a.example", "b.example"), signals=()),
        PrimaryDomain("a.example", tied=True, tied_domains=("a.example", "b.example")),
        mode=DataMode.SYNTHETIC,
        store=Store(),
        resolver_factory=factory,
        now=datetime(2026, 10, 5, tzinfo=UTC),
    )

    assert outcome.source is TieSource.SYNTHETIC_PROVISIONAL
    assert outcome.flagged  # reported on the run, as 8.18 requires
    assert built == []
    guard.assert_clean()


# Verifies: specs/lead-source-adapters/requirements.md#4.1
async def test_a_source_resolved_live_with_fixtures_is_not_a_synthetic_run(
    engine: Engine,
    clean_environment: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard = guard_for_mode(DataMode.SYNTHETIC)
    guard.install(monkeypatch)
    registry = SourceRegistry(
        [ApolloSource], {"apollo": SourceSettings(mode=DataMode.LIVE)}
    )

    def fixtures_under_live(
        source_class: type[BaseLeadSource], mode: DataMode, pacing: SourcePacing | None
    ) -> BaseLeadSource:
        transport = source_class.build_transport(DataMode.SYNTHETIC)
        return ApolloSource(
            mode,
            transport=transport,
            environ={"APOLLO_API_KEY": "x"},
            pacing=pacing,
            vocabulary={"t": ["datastax"]},
        )

    pipeline = await run_pipeline(engine, registry, fixtures_under_live)

    assert guard.violations == []  # no socket: the fixtures were used
    assert pipeline.results[0].mode_reason.startswith("per-source override")
    with pytest.raises(AssertionError, match="apollo"):
        assert_pure_synthetic(pipeline, guard, frozenset({"apollo"}))


# Verifies: specs/lead-source-adapters/requirements.md#4.1
# Verifies: specs/lead-source-adapters/requirements.md#11.5
async def test_hunters_verifier_answers_from_its_fixture_under_the_guard(
    clean_environment: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    guard = guard_for_mode(DataMode.SYNTHETIC)
    guard.install(monkeypatch)
    served = spy_on_fixture_transports(monkeypatch)
    source = build_synthetic(HunterSource, DataMode.SYNTHETIC, None)
    work = (
        make_lead("apollo", company__domain="alpha.example"),
        make_lead(
            "apollo",
            person__first_name="Ada",
            person__last_name="Lovelace",
            company__domain="beta.example",
        ),
        make_lead("apollo", person__email="grace.hopper@example.org"),
    )

    batch = await source.fetch_raw(EnrichmentRequest(kind="enrich", work_list=work))
    contributions = source.normalize_checked(batch)

    assert isinstance(source.transport, FixtureTransport)
    assert sorted(set(served)) == [
        "hunter:/v2/domain-search",
        "hunter:/v2/email-finder",
        "hunter:/v2/email-verifier",
    ]
    assert contributions
    assert clean_environment == []
    guard.assert_clean()
