"""Plan-dependent rate limits read at run time, and their documentation.

A provider's limit can depend on the operator's plan, so an adapter may size its
buckets from a non-secret environment setting when a live run starts
(``run_rate_limit``). The composition root asks for it, for live sources only, before
the run record exists (follow-up 2026-10-06: a bad value is a configuration error
before any write) and hands the answers to the orchestrator, which reads no
environment and paces a live source on what it was handed. Such settings are declared
in ``optional_env`` and documented in the generated ``.env.example`` with their
``env_notes``.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar

import pytest

from leadforge.lead_ingestion.base_source import (
    BaseLeadSource,
    Capability,
    ChargeUnit,
    CostClass,
    Endpoint,
    LeadContribution,
    RateBucket,
    RateWindow,
    RawBatch,
    SourceRequest,
)
from leadforge.lead_ingestion.env_example import ManifestError, render_env_example
from leadforge.lead_ingestion.ingest_runner import live_rate_limits
from leadforge.lead_ingestion.mode_resolution import ModeResolution
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.orchestrator import IngestionOrchestrator
from leadforge.lead_ingestion.pacing import SourcePacing
from leadforge.lead_ingestion.registry import SourceRegistry, SourceSettings

DECLARED = RateBucket("api", (RateWindow(1, 1.0),), True, "https://docs.example.test")
PLANNED = RateBucket("api", (RateWindow(7, 60.0),), True, "https://docs.example.test")
COMMITTED = Path(__file__).parents[4] / ".env.example"


class Planned(BaseLeadSource):
    name: ClassVar[str] = "planned"
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.SEARCH})
    rate_limit: ClassVar[Mapping[str, RateBucket]] = {"api": DECLARED}
    answerable_surfaces: ClassVar[Mapping[str, frozenset[str]]] = {}
    cost_class: ClassVar[CostClass] = CostClass.FREE
    charge_unit: ClassVar[ChargeUnit] = ChargeUnit.PER_CALL
    yields_suppression: ClassVar[bool] = False
    target_vocabulary: ClassVar[Mapping[str, object]] = {}
    endpoints: ClassVar[Mapping[str, Endpoint]] = {}
    required_env: ClassVar[tuple[str, ...]] = ()
    asked: ClassVar[list[Mapping[str, str] | None]] = []

    @classmethod
    def run_rate_limit(
        cls, environ: Mapping[str, str] | None = None
    ) -> Mapping[str, RateBucket]:
        cls.asked.append(environ)
        return {"api": PLANNED}

    async def fetch_raw(self, request: SourceRequest) -> RawBatch:
        return RawBatch(source_name=self.name, payload={})

    def normalize(self, raw: RawBatch) -> list[LeadContribution]:
        return []


class Declared(Planned):
    name: ClassVar[str] = "declared"

    @classmethod
    def run_rate_limit(
        cls, environ: Mapping[str, str] | None = None
    ) -> Mapping[str, RateBucket]:
        return super(Planned, cls).run_rate_limit(environ)


async def _run(
    mode: DataMode,
    source_class: type[BaseLeadSource],
    live_rate_limits: Mapping[str, Mapping[str, RateBucket]] | None = None,
) -> SourcePacing:
    seen: dict[str, SourcePacing | None] = {}

    def resolve(_: type[BaseLeadSource], __: SourceSettings) -> ModeResolution:
        return ModeResolution(mode, "test")

    def build(
        cls: type[BaseLeadSource], m: DataMode, pacing: SourcePacing | None
    ) -> BaseLeadSource:
        seen[cls.name] = pacing
        return cls(m)

    orchestrator = IngestionOrchestrator(
        SourceRegistry([source_class]),
        resolve_mode=resolve,
        build_source=build,
        max_concurrent_sources=1,
        run_timeout_s=30,
        live_rate_limits=live_rate_limits,
    )
    await orchestrator.run(SourceRequest(kind="search"))
    return seen[source_class.name]  # type: ignore[return-value]


# Verifies: specs/lead-source-adapters/requirements.md#7.1
def test_the_default_run_rate_limit_is_the_declared_rate_limit() -> None:
    assert Declared.run_rate_limit({}) is Declared.rate_limit
    assert Declared.run_rate_limit() is Declared.rate_limit


# Verifies: specs/lead-source-adapters/requirements.md#7.1
async def test_a_live_source_is_paced_on_the_limits_it_is_handed() -> None:
    Planned.asked.clear()
    pacing = await _run(DataMode.LIVE, Planned, {"planned": {"api": PLANNED}})
    assert pacing is not None
    assert Planned.asked == []  # the orchestrator reads no setting itself
    assert pacing.throttle.bucket("api").available() == (7.0,)


# Verifies: specs/lead-source-adapters/requirements.md#7.1
async def test_a_live_source_handed_no_limits_is_paced_on_its_declaration() -> None:
    Planned.asked.clear()
    pacing = await _run(DataMode.LIVE, Planned)
    assert pacing is not None
    assert Planned.asked == []
    assert pacing.throttle.bucket("api").available() == (1.0,)


# Verifies: specs/lead-source-adapters/requirements.md#7.1
# Verifies: specs/lead-source-adapters/requirements.md#7.5
def test_the_root_reads_plan_limits_for_live_sources_only() -> None:
    Planned.asked.clear()
    registry = SourceRegistry([Planned, Declared])
    modes = {"planned": DataMode.LIVE, "declared": DataMode.SYNTHETIC}

    def resolve(cls: type[BaseLeadSource], _: SourceSettings) -> ModeResolution:
        return ModeResolution(modes[cls.name], "test")

    environ = {"PLAN": "x"}
    limits = live_rate_limits(registry, resolve, environ)

    assert limits == {"planned": {"api": PLANNED}}
    assert Planned.asked == [environ]  # the environment it was handed, once


# Verifies: specs/lead-source-adapters/requirements.md#7.5
async def test_a_synthetic_source_never_reads_its_plan_setting() -> None:
    Planned.asked.clear()
    assert await _run(DataMode.SYNTHETIC, Planned) is None
    assert Planned.asked == []


def _cls(
    name: str,
    env: tuple[str, ...],
    optional: tuple[str, ...] = (),
    notes: Mapping[str, str] | None = None,
) -> type[BaseLeadSource]:
    return type(
        f"{name.title()}Source",
        (Planned,),
        {
            "name": name,
            "required_env": env,
            "optional_env": optional,
            "env_notes": notes or {},
            "docs_url": f"https://docs.example.test/{name}",
        },
    )


def _block(text: str, variable: str) -> list[str]:
    lines = text.splitlines()
    at = next(i for i, ln in enumerate(lines) if ln.startswith(f"{variable}="))
    out: list[str] = []
    for ln in reversed(lines[:at]):
        if not ln.startswith("#"):
            break
        out.append(ln)
    return list(reversed(out))


# Verifies: specs/lead-source-adapters/requirements.md#10.4
def test_optional_settings_are_documented_with_their_note_and_docs_url() -> None:
    cls = _cls(
        "alpha",
        ("ALPHA_API_KEY",),
        ("ALPHA_PLAN",),
        {"ALPHA_PLAN": "optional: plan name; unset means free"},
    )
    text = render_env_example(SourceRegistry([cls]))
    assert _block(text, "ALPHA_PLAN") == [
        "# optional: plan name; unset means free",
        "# alpha docs: https://docs.example.test/alpha",
    ]
    assert "ALPHA_PLAN=\n" in text


# Verifies: specs/lead-source-adapters/requirements.md#10.2
def test_a_required_variable_can_carry_a_note() -> None:
    cls = _cls("beta", ("BETA_TOKEN",), notes={"BETA_TOKEN": "needs scope a.read"})
    text = render_env_example(SourceRegistry([cls]))
    assert _block(text, "BETA_TOKEN")[0] == "# needs scope a.read"


# Verifies: specs/lead-source-adapters/requirements.md#10.2
@pytest.mark.parametrize(
    "notes",
    [
        {"GAMMA_KEY": "two\nlines"},
        {"GAMMA_KEY": " "},
        {"UNDECLARED": "a note for a variable nobody reads"},
    ],
)
def test_an_unusable_note_is_rejected(notes: Mapping[str, str]) -> None:
    cls = _cls("gamma", ("GAMMA_KEY",), notes=notes)
    with pytest.raises(ManifestError):
        render_env_example(SourceRegistry([cls]))


# Verifies: specs/lead-source-adapters/requirements.md#10.2
def test_two_adapters_disagreeing_on_a_shared_note_is_rejected() -> None:
    one = _cls("one", ("SHARED_KEY",), notes={"SHARED_KEY": "first"})
    two = _cls("two", ("SHARED_KEY",), notes={"SHARED_KEY": "second"})
    with pytest.raises(ManifestError):
        render_env_example(SourceRegistry([one, two]))


# Verifies: specs/lead-source-adapters/requirements.md#10.4
def test_every_registered_optional_setting_appears_in_committed_file() -> None:
    registry = SourceRegistry.discover()
    text = COMMITTED.read_text()
    declared = [
        variable
        for name in registry.names()
        for variable in registry.source_class(name).optional_env
    ]
    assert declared  # the plan settings of the shipped adapters
    for variable in declared:
        assert f"\n{variable}=\n" in text, f"undocumented optional {variable}"
