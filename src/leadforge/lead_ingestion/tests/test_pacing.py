"""Task 10.3: no throttle and no retry policy exist for a synthetic source."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

import leadforge.lead_ingestion.pacing as pacing_module
import leadforge.lead_ingestion.throttle as throttle_module
from leadforge.lead_ingestion.base_source import RateBucket, RateWindow
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.pacing import SourcePacing, build_pacing
from leadforge.lead_ingestion.retry import RetryPolicy
from leadforge.lead_ingestion.throttle import CompositeTokenBucket, SourceThrottle

BUCKETS: Mapping[str, RateBucket] = {
    "search": RateBucket(
        name="search",
        windows=(RateWindow(requests=5, per_seconds=1.0),),
        documented=True,
        doc_url="https://example.invalid/limits",
    ),
    "burst": RateBucket(
        name="burst",
        windows=(RateWindow(requests=10, per_seconds=10.0),),
        documented=False,
        doc_url="",
    ),
}


class Spy:
    """Counts constructions of a class while still building the real thing."""

    def __init__(self, real: type[Any]) -> None:
        self.real = real
        self.calls = 0

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        return self.real(*args, **kwargs)


@pytest.fixture
def spies(monkeypatch: pytest.MonkeyPatch) -> dict[str, Spy]:
    found = {
        "throttle": Spy(SourceThrottle),
        "bucket": Spy(CompositeTokenBucket),
        "retry": Spy(RetryPolicy),
    }
    monkeypatch.setattr(pacing_module, "SourceThrottle", found["throttle"])
    monkeypatch.setattr(throttle_module, "CompositeTokenBucket", found["bucket"])
    monkeypatch.setattr(pacing_module, "RetryPolicy", found["retry"])
    return found


# Verifies: specs/lead-source-adapters/requirements.md#7.5
def test_synthetic_constructs_neither_bucket_nor_retry_policy(
    spies: dict[str, Spy],
) -> None:
    result = build_pacing("alpha", BUCKETS, DataMode.SYNTHETIC)

    assert result is None
    assert {k: s.calls for k, s in spies.items()} == {
        "throttle": 0,
        "bucket": 0,
        "retry": 0,
    }


# Verifies: specs/lead-source-adapters/requirements.md#7.5
def test_live_constructs_one_bucket_per_declared_bucket_and_a_retry_policy(
    spies: dict[str, Spy],
) -> None:
    result = build_pacing("alpha", BUCKETS, DataMode.LIVE)

    assert isinstance(result, SourcePacing)
    assert isinstance(result.throttle, SourceThrottle)
    assert isinstance(result.retry, RetryPolicy)
    assert set(result.throttle.bucket_names()) == {"search", "burst"}
    assert spies["throttle"].calls == 1
    assert spies["bucket"].calls == 2
    assert spies["retry"].calls == 1


# Verifies: specs/lead-source-adapters/requirements.md#7.5
def test_each_live_source_gets_its_own_pacing() -> None:
    first = build_pacing("alpha", BUCKETS, DataMode.LIVE)
    second = build_pacing("beta", BUCKETS, DataMode.LIVE)

    assert first is not None
    assert second is not None
    assert first.throttle is not second.throttle
    assert first.throttle.source_name == "alpha"
    assert second.throttle.source_name == "beta"


# Verifies: specs/lead-source-adapters/requirements.md#7.5
def test_live_pacing_is_usable_end_to_end() -> None:
    result = build_pacing("alpha", BUCKETS, DataMode.LIVE)
    assert result is not None
    assert result.throttle.bucket("search").try_acquire() is True
    assert result.retry.max_attempts >= 1


# Verifies: specs/lead-source-adapters/requirements.md#7.5
def test_unknown_mode_is_rejected_not_treated_as_live() -> None:
    with pytest.raises(ValueError, match="mode"):
        build_pacing("alpha", BUCKETS, "other")  # type: ignore[arg-type]


# Verifies: specs/lead-source-adapters/requirements.md#7.5
def test_live_source_with_no_declared_buckets_still_gets_pacing() -> None:
    result = build_pacing("alpha", {}, DataMode.LIVE)

    assert result is not None
    assert result.throttle.bucket_names() == ()
    assert isinstance(result.retry, RetryPolicy)
