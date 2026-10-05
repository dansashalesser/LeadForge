"""The adapter is handed its transport; the endpoint map is the adapter's own (12.1)."""

from collections.abc import Mapping
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
from leadforge.lead_ingestion.errors import UndeclaredEndpointError
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.transport import (
    FixtureTransport,
    RestTransport,
    Transport,
    TransportResponse,
)

BUCKET = RateBucket(
    name="default",
    windows=(RateWindow(requests=60, per_seconds=60.0),),
    documented=True,
    doc_url="https://example.com/rate-limits",
)
SEARCH = Endpoint(method="POST", path="/v1/search", bucket="default")
OTHER = Endpoint(method="GET", path="/v1/other", bucket="default")


class Provider(BaseLeadSource):
    name: ClassVar[str] = "provider"
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.SEARCH})
    rate_limit: ClassVar[Mapping[str, RateBucket]] = {"default": BUCKET}
    answerable_surfaces: ClassVar[Mapping[str, frozenset[str]]] = {}
    cost_class: ClassVar[CostClass] = CostClass.FREE
    charge_unit: ClassVar[ChargeUnit] = ChargeUnit.PER_CALL
    yields_suppression: ClassVar[bool] = False
    target_vocabulary: ClassVar[Mapping[str, object]] = {}
    endpoints: ClassVar[Mapping[str, Endpoint]] = {"search": SEARCH}
    required_env: ClassVar[tuple[str, ...]] = ()
    base_url: ClassVar[str] = "https://api.example.com"

    async def fetch_raw(self, request: SourceRequest) -> RawBatch:
        return RawBatch(source_name=self.name, payload=None)

    def normalize(self, raw: RawBatch) -> list[LeadContribution]:
        return []


class Recorder:
    async def send(
        self,
        endpoint: Endpoint,
        *,
        params: Mapping[str, object] | None,
        json_body: Mapping[str, object] | None,
        headers: Mapping[str, str],
    ) -> TransportResponse:
        return TransportResponse(status=200, headers={}, body=None)


# Verifies: specs/lead-source-adapters/requirements.md#12.1
def test_adapter_holds_the_transport_it_was_given() -> None:
    transport: Transport = Recorder()
    source = Provider(DataMode.SYNTHETIC, transport=transport)
    assert source.transport is transport


# Verifies: specs/lead-source-adapters/requirements.md#12.1
def test_unbound_adapter_refuses_to_hand_out_a_transport() -> None:
    source = Provider(DataMode.SYNTHETIC)
    with pytest.raises(RuntimeError, match="provider"):
        _ = source.transport


# Verifies: specs/lead-source-adapters/requirements.md#12.1
async def test_synthetic_transport_is_built_from_the_adapters_own_endpoints() -> None:
    transport = Provider.build_transport(DataMode.SYNTHETIC)
    assert isinstance(transport, FixtureTransport)
    with pytest.raises(UndeclaredEndpointError):
        await transport.send(OTHER, params=None, json_body=None, headers={})


# Verifies: specs/lead-source-adapters/requirements.md#12.1
async def test_live_transport_is_built_from_the_adapters_own_endpoints() -> None:
    transport = Provider.build_transport(DataMode.LIVE)
    assert isinstance(transport, RestTransport)
    try:
        with pytest.raises(UndeclaredEndpointError):
            await transport.send(OTHER, params=None, json_body=None, headers={})
    finally:
        await transport.aclose()


# Verifies: specs/lead-source-adapters/requirements.md#12.1
def test_live_transport_without_a_declared_host_is_refused() -> None:
    class NoHost(Provider):
        name: ClassVar[str] = "nohost"
        base_url: ClassVar[str] = ""

    with pytest.raises(ValueError, match="nohost"):
        NoHost.build_transport(DataMode.LIVE)


# Verifies: specs/lead-source-adapters/requirements.md#12.1
@pytest.mark.parametrize(
    "url", ["http://api.example.com", "api.example.com", "ftp://x"]
)
def test_live_transport_over_anything_but_https_is_refused(url: str) -> None:
    class Cleartext(Provider):
        name: ClassVar[str] = "cleartext"
        base_url: ClassVar[str] = url

    with pytest.raises(ValueError, match="https"):
        Cleartext.build_transport(DataMode.LIVE)


# Verifies: specs/lead-source-adapters/requirements.md#12.1
def test_synthetic_mode_builds_no_live_transport_even_without_a_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*_: object, **__: object) -> None:
        raise AssertionError("live transport built in synthetic mode")

    monkeypatch.setattr("leadforge.lead_ingestion.transport.RestTransport", refuse)

    class NoHost(Provider):
        name: ClassVar[str] = "nohost"
        base_url: ClassVar[str] = ""

    assert isinstance(NoHost.build_transport(DataMode.SYNTHETIC), FixtureTransport)
