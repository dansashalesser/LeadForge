"""``BaseLeadSource.from_run``: how a composition root builds any source (task 20).

The root knows no concrete adapter, so it builds every source the same way and hands
over the Target Profile's effective vocabulary for it. A source that has no use for the
vocabulary ignores it; one that does (see tests/adapters/test_from_run_vocabulary.py)
overrides this one classmethod.
"""

from collections.abc import Mapping
from typing import ClassVar

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
from leadforge.lead_ingestion.models import DataMode


class Plain(BaseLeadSource):
    name: ClassVar[str] = "plain"
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
        return []


# Verifies: specs/lead-source-adapters/requirements.md#4.5
def test_the_default_from_run_builds_the_source_over_the_given_transport() -> None:
    transport = Plain.build_transport(DataMode.SYNTHETIC)

    source = Plain.from_run(
        DataMode.SYNTHETIC,
        transport=transport,
        pacing=None,
        vocabulary={"anything": ["ignored"]},
    )

    assert isinstance(source, Plain)
    assert source.data_mode is DataMode.SYNTHETIC
    assert source.transport is transport


# Verifies: specs/lead-source-adapters/requirements.md#4.5
def test_the_default_from_run_accepts_no_vocabulary() -> None:
    source = Plain.from_run(
        DataMode.SYNTHETIC,
        transport=Plain.build_transport(DataMode.SYNTHETIC),
        pacing=None,
        vocabulary=None,
    )

    assert isinstance(source, Plain)
