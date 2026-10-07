"""Task 9.3: provider-issued targeting identifiers are checked at live startup."""

from __future__ import annotations

from collections.abc import Collection, Mapping
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
    RawBatch,
    SourceRequest,
)
from leadforge.lead_ingestion.errors import ConfigurationError
from leadforge.lead_ingestion.identifier_validation import (
    UnrecognisedIdentifier,
    validate_identifiers,
)
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.registry import SourceRegistry
from leadforge.lead_ingestion.target_profile import TargetProfile


class Issuer(BaseLeadSource):
    name: ClassVar[str] = "issuer"
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.SEARCH})
    rate_limit: ClassVar[Mapping[str, RateBucket]] = {}
    answerable_surfaces: ClassVar[Mapping[str, frozenset[str]]] = {}
    cost_class: ClassVar[CostClass] = CostClass.FREE
    charge_unit: ClassVar[ChargeUnit] = ChargeUnit.PER_CALL
    yields_suppression: ClassVar[bool] = False
    target_vocabulary: ClassVar[Mapping[str, object]] = {"default_term": "id_default"}
    endpoints: ClassVar[Mapping[str, Endpoint]] = {}
    required_env: ClassVar[tuple[str, ...]] = ()

    async def fetch_raw(self, request: SourceRequest) -> RawBatch:
        raise NotImplementedError

    def normalize(self, raw: RawBatch) -> list[LeadContribution]:
        raise NotImplementedError


class Phrases(Issuer):
    name: ClassVar[str] = "phrases"


PROFILE = TargetProfile(
    technologies={
        "alpha": {"issuer": "id_alpha", "phrases": ["alpha tool"]},
        "beta": {"issuer": ["id_beta", "id_stale"]},
        "default_term": {},
    },
    competitors={"gamma": {"issuer": "id_gone"}},
)
REGISTRY = SourceRegistry([Issuer, Phrases])
KNOWN = {"id_alpha", "id_beta", "id_default"}


class Lookup:
    def __init__(self, known: Collection[str]) -> None:
        self.known = known
        self.calls = 0

    async def __call__(self) -> Collection[str]:
        self.calls += 1
        return self.known


async def check(
    lookup: Lookup, mode: DataMode = DataMode.LIVE, profile: TargetProfile = PROFILE
) -> tuple[UnrecognisedIdentifier, ...]:
    return await validate_identifiers(
        profile, REGISTRY, {"issuer": lookup}, mode, path="config/target_profile.yaml"
    )


# Verifies: specs/lead-source-adapters/requirements.md#23.4
async def test_stale_identifiers_are_named_with_source_and_term() -> None:
    found = await check(Lookup(KNOWN))
    assert [(w.source, w.term, w.identifier) for w in found] == [
        ("issuer", "beta", "id_stale"),
        ("issuer", "gamma", "id_gone"),
    ]
    text = str(found[0])
    assert "issuer" in text
    assert "beta" in text
    assert "id_stale" in text


# Verifies: specs/lead-source-adapters/requirements.md#23.4
async def test_all_recognised_identifiers_give_no_warning() -> None:
    assert await check(Lookup(KNOWN | {"id_stale", "id_gone"})) == ()


# Verifies: specs/lead-source-adapters/requirements.md#23.4
async def test_synthetic_mode_never_calls_the_provider() -> None:
    lookup = Lookup(set())
    assert await check(lookup, DataMode.SYNTHETIC) == ()
    assert lookup.calls == 0


# Verifies: specs/lead-source-adapters/requirements.md#23.4
async def test_unknown_mode_is_rejected_not_treated_as_live() -> None:
    with pytest.raises(ValueError, match="unknown data mode"):
        await check(Lookup(KNOWN), "live")  # type: ignore[arg-type]


# Verifies: specs/lead-source-adapters/requirements.md#23.4
async def test_adapter_default_vocabulary_is_checked_when_profile_has_no_column() -> (
    None
):
    blanked = TargetProfile(technologies={"default_term": {"issuer": ""}})
    # an explicit empty column drops the default, so nothing is checked
    assert await check(Lookup(set()), profile=blanked) == ()
    bare = TargetProfile(technologies={"default_term": {"phrases": "x"}})
    found = await check(Lookup(set()), profile=bare)
    assert [(w.term, w.identifier) for w in found] == [("default_term", "id_default")]


# Verifies: specs/lead-source-adapters/requirements.md#23.4
async def test_lookup_runs_once_per_source_and_not_without_identifiers() -> None:
    lookup = Lookup(KNOWN)
    await check(lookup)
    assert lookup.calls == 1
    idle = Lookup(KNOWN)
    await check(idle, profile=TargetProfile(technologies={"x": {"phrases": "p"}}))
    assert idle.calls == 0


# Verifies: specs/lead-source-adapters/requirements.md#23.4
async def test_a_failing_lookup_propagates_rather_than_passing_silently() -> None:
    class Down(Lookup):
        async def __call__(self) -> Collection[str]:
            raise ConnectionError("provider down")

    with pytest.raises(ConnectionError):
        await check(Down(KNOWN))


# Verifies: specs/lead-source-adapters/requirements.md#23.4
async def test_a_lookup_for_an_unregistered_source_is_an_error() -> None:
    with pytest.raises(KeyError):
        await validate_identifiers(
            PROFILE, REGISTRY, {"nobody": Lookup(KNOWN)}, DataMode.LIVE, path="p.yaml"
        )


# Verifies: specs/lead-source-adapters/requirements.md#23.4
async def test_non_text_identifier_is_a_named_error_without_the_value() -> None:
    profile = TargetProfile(technologies={"alpha": {"issuer": {"uid": "SECRET_VALUE"}}})
    with pytest.raises(ConfigurationError) as raised:
        await check(Lookup(KNOWN), profile=profile)
    assert "alpha" in str(raised.value)
    assert "SECRET_VALUE" not in str(raised.value)


# Verifies: specs/lead-source-adapters/requirements.md#23.4
@pytest.mark.parametrize("bad", [["ok", 7], 7, True, ["ok", None]])
async def test_wrong_identifier_shapes_are_rejected_not_coerced(bad: object) -> None:
    profile = TargetProfile(technologies={"alpha": {"issuer": bad}})
    with pytest.raises(ConfigurationError):
        await check(Lookup(KNOWN), profile=profile)


# Verifies: specs/lead-source-adapters/requirements.md#23.4
async def test_identifiers_are_matched_exactly_not_by_case_or_whitespace() -> None:
    profile = TargetProfile(
        technologies={"a": {"issuer": ["ID_ALPHA", " id_beta", "id_alpha"]}}
    )
    found = await check(Lookup(KNOWN), profile=profile)
    assert [w.identifier for w in found] == ["ID_ALPHA", " id_beta"]


# Verifies: specs/lead-source-adapters/requirements.md#23.4
async def test_a_failing_lookup_is_not_masked_by_earlier_results() -> None:
    class Down(Lookup):
        async def __call__(self) -> Collection[str]:
            raise ConnectionError("provider down")

    with pytest.raises(ConnectionError):
        await validate_identifiers(
            PROFILE,
            REGISTRY,
            {"issuer": Lookup(KNOWN), "phrases": Down(KNOWN)},
            DataMode.LIVE,
            path="p.yaml",
        )


# Verifies: specs/lead-source-adapters/requirements.md#23.4
async def test_warnings_follow_lookup_order_then_profile_order() -> None:
    both = {"phrases": Lookup(set()), "issuer": Lookup(set())}
    found = await validate_identifiers(
        PROFILE, REGISTRY, both, DataMode.LIVE, path="p.yaml"
    )
    sources = [w.source for w in found]
    assert sources == sorted(sources, key=["phrases", "issuer"].index)
    assert sources[0] == "phrases"


# Verifies: specs/lead-source-adapters/requirements.md#23.4
@pytest.mark.parametrize("bad", ["abc", b"abc"])
async def test_a_lookup_returning_one_bare_string_is_rejected_not_split(
    bad: object,
) -> None:
    class Bare(Lookup):
        async def __call__(self) -> Collection[str]:
            return bad  # type: ignore[return-value]

    with pytest.raises(TypeError, match="lookup"):
        await validate_identifiers(
            PROFILE,
            REGISTRY,
            {"issuer": Bare(KNOWN), "phrases": Lookup(KNOWN)},
            DataMode.LIVE,
            path="p.yaml",
        )
