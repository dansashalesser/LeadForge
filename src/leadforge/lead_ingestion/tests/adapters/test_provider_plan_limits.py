"""Plan-dependent provider limits and their documentation (follow-up, 2026-10-06).

SerpApi's hourly throughput and Apollo's minute, hour and day windows depend on the
operator's plan, so each is a non-secret environment setting read when a live run
starts, with the most conservative documented plan as the default. Figures are from
the providers' pricing and rate-limit pages (search-engine extracts; the pages
themselves were network-blocked when checked).
"""

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from leadforge.lead_ingestion.adapters.apollo import ApolloSource
from leadforge.lead_ingestion.adapters.google_search import GoogleSearchSource
from leadforge.lead_ingestion.adapters.hubspot import HubSpotSource
from leadforge.lead_ingestion.adapters.search_backends.serpapi import (
    DEFAULT_HOURLY_LIMIT,
    HOURLY_LIMIT_ENV,
    SerpApiBackend,
)
from leadforge.lead_ingestion.base_source import RateBucket, RateWindow
from leadforge.lead_ingestion.errors import ConfigurationError
from leadforge.lead_ingestion.throttle import CompositeTokenBucket

FIXTURES = Path(__file__).parents[2] / "fixtures"
SERPAPI_PRICING = "https://serpapi.com/pricing"
APOLLO_RATE_LIMITS = "https://docs.apollo.io/reference/rate-limits"


def _only(limits: Mapping[str, RateBucket]) -> RateBucket:
    (bucket,) = limits.values()
    return bucket


# --- SerpApi -----------------------------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#7.1
def test_serpapi_defaults_to_the_free_plan_hourly_throughput() -> None:
    assert DEFAULT_HOURLY_LIMIT == 50
    bucket = GoogleSearchSource.rate_limit[
        GoogleSearchSource.endpoints["search"].bucket
    ]
    assert bucket.windows == (
        RateWindow(requests=50, per_seconds=3600.0),
        RateWindow(requests=1, per_seconds=72.0),
    )
    assert bucket.documented is True
    assert bucket.doc_url == SERPAPI_PRICING
    assert _only(GoogleSearchSource.run_rate_limit({})) == bucket


# Verifies: specs/lead-source-adapters/requirements.md#7.1
@pytest.mark.parametrize(
    ("value", "hourly"), [("1000", 1000), (" 200 ", 200), ("6000", 6000), ("", 50)]
)
def test_serpapi_hourly_limit_is_read_from_the_environment(
    value: str, hourly: int
) -> None:
    bucket = _only(GoogleSearchSource.run_rate_limit({HOURLY_LIMIT_ENV: value}))
    assert bucket.name == GoogleSearchSource.endpoints["search"].bucket
    assert bucket.windows == (
        RateWindow(requests=hourly, per_seconds=3600.0),
        RateWindow(requests=1, per_seconds=3600.0 / hourly),
    )


# Verifies: specs/lead-source-adapters/requirements.md#7.1 (property)
@pytest.mark.parametrize("hourly", [1, 2, 3, 7, 50, 199, 200, 1000, 3000, 6000, 99_999])
def test_serpapi_pacing_never_admits_more_than_the_hourly_limit(hourly: int) -> None:
    hour, spacing = SerpApiBackend.rate_bucket_for(
        {HOURLY_LIMIT_ENV: str(hourly)}
    ).windows
    assert hour.requests == hourly
    assert hour.per_seconds == 3600.0
    # Even spacing alone admits at most ``hourly`` calls in any hour.
    assert spacing.requests == 1
    assert spacing.per_seconds * hourly == pytest.approx(3600.0)


# Verifies: specs/lead-source-adapters/requirements.md#7.1
@pytest.mark.parametrize(
    "value", ["0", "-5", "abc", "1.5", "1e3", "\u0661\u0660", "9" * 12]
)
def test_an_unusable_serpapi_hourly_limit_is_a_configuration_error(value: str) -> None:
    with pytest.raises(ConfigurationError) as caught:
        GoogleSearchSource.run_rate_limit({HOURLY_LIMIT_ENV: value})
    assert HOURLY_LIMIT_ENV in str(caught.value)
    assert value not in str(caught.value)


# Verifies: specs/lead-source-adapters/requirements.md#10.4
def test_serpapi_hourly_limit_is_a_documented_optional_setting() -> None:
    assert GoogleSearchSource.optional_env == (HOURLY_LIMIT_ENV,)
    assert HOURLY_LIMIT_ENV not in GoogleSearchSource.required_env
    assert "50" in GoogleSearchSource.env_notes[HOURLY_LIMIT_ENV]


# --- Apollo ------------------------------------------------------------------------

# (search, match) per minute, hour, day; None = no limit in that window. From
# https://docs.apollo.io/reference/rate-limits (updated 2026-08-21, read 2026-10-06):
# people/match is an Enrichment endpoint, 50/200/600 on Free and 1,000 a minute with
# "No hourly limit" and "No daily limit" on every paid plan.
_Plan = tuple[tuple[int | None, ...], tuple[int | None, ...]]
FREE: _Plan = ((50, 200, 600), (50, 200, 600))
PAID: _Plan = ((200, 6000, 50_000), (1000, None, None))


def _windows(*limits: int | None) -> tuple[RateWindow, ...]:
    spans = (60.0, 3600.0, 86_400.0)
    return tuple(
        RateWindow(requests=n, per_seconds=span)
        for n, span in zip(limits, spans, strict=True)
        if n is not None
    )


# Verifies: specs/lead-source-adapters/requirements.md#12.11
def test_apollo_search_and_match_are_paced_on_separate_buckets() -> None:
    assert ApolloSource.endpoints["search"].bucket == "search"
    assert ApolloSource.endpoints["match"].bucket == "match"
    assert set(ApolloSource.rate_limit) == {"search", "match"}


# Verifies: specs/lead-source-adapters/requirements.md#7.1
def test_apollo_defaults_to_the_free_plan_windows() -> None:
    search, match = FREE
    assert ApolloSource.rate_limit["search"].windows == _windows(*search)
    assert ApolloSource.rate_limit["match"].windows == _windows(*match)
    for bucket in ApolloSource.rate_limit.values():
        assert bucket.documented is True
        assert bucket.doc_url == APOLLO_RATE_LIMITS
    assert ApolloSource.run_rate_limit({}) == ApolloSource.rate_limit


# Verifies: specs/lead-source-adapters/requirements.md#7.1
@pytest.mark.parametrize(
    ("plan", "expected"),
    [
        ("free", FREE),
        ("basic", PAID),
        ("professional", PAID),
        (" Organization ", PAID),
        ("", FREE),
    ],
)
def test_apollo_plan_selects_the_documented_windows(plan: str, expected: _Plan) -> None:
    limits = ApolloSource.run_rate_limit({"APOLLO_PLAN": plan})
    assert limits["search"].windows == _windows(*expected[0])
    assert limits["match"].windows == _windows(*expected[1])
    assert {b.name for b in limits.values()} == {"search", "match"}


# Verifies: specs/lead-source-adapters/requirements.md#7.1
@pytest.mark.parametrize("plan", ["enterprise", "free plan", "1"])
def test_an_unknown_apollo_plan_is_a_configuration_error(plan: str) -> None:
    with pytest.raises(ConfigurationError) as caught:
        ApolloSource.run_rate_limit({"APOLLO_PLAN": plan})
    assert "APOLLO_PLAN" in str(caught.value)
    assert plan not in str(caught.value)


# Verifies: specs/lead-source-adapters/requirements.md#10.4
def test_apollo_plan_is_a_documented_optional_setting() -> None:
    assert ApolloSource.optional_env == ("APOLLO_PLAN",)
    note = ApolloSource.env_notes["APOLLO_PLAN"]
    for plan in ("free", "basic", "professional", "organization"):
        assert plan in note


# --- Throttling on the tightest window ---------------------------------------------


class _FakeClock:
    """Deterministic time: ``sleep`` advances ``now`` and never really waits."""

    def __init__(self) -> None:
        self.t = 1000.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


def _throttled(bucket: RateBucket) -> tuple[CompositeTokenBucket, _FakeClock]:
    clock = _FakeClock()
    throttle = CompositeTokenBucket(
        "prov", bucket, clock=clock.now, sleep=clock.sleep, max_retry_after_s=3600.0
    )
    return throttle, clock


# Verifies: specs/lead-source-adapters/requirements.md#7.1
async def test_apollo_free_search_binds_on_the_minute_window_then_the_hour() -> None:
    throttle, clock = _throttled(ApolloSource.run_rate_limit({})["search"])
    for _ in range(50):  # the minute window's burst
        await throttle.acquire()
    assert clock.sleeps == []
    await throttle.acquire()  # minute window binds: 60/50 s, not the hour's 18 s
    assert clock.sleeps == [pytest.approx(1.2)]
    while not any(s > 2.0 for s in clock.sleeps):
        await throttle.acquire()
    # Once the hour's 200 are spent, its refill (one per 18 s) binds instead.
    assert clock.t - 1000.0 < 3600.0
    await throttle.acquire()
    assert clock.sleeps[-1] == pytest.approx(18.0)


# Verifies: specs/lead-source-adapters/requirements.md#7.1
async def test_serpapi_default_bucket_spaces_calls_and_caps_the_hour() -> None:
    throttle, clock = _throttled(GoogleSearchSource.run_rate_limit({})["default"])
    for _ in range(51):
        await throttle.acquire()
    assert clock.sleeps == [pytest.approx(72.0)] * 50  # never a burst
    assert clock.t - 1000.0 == pytest.approx(3600.0)  # 51 calls span a full hour


# --- HubSpot -----------------------------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#10.2
def test_hubspot_token_documents_both_read_scopes() -> None:
    note = HubSpotSource.env_notes["HUBSPOT_ACCESS_TOKEN"]
    assert "crm.objects.contacts.read" in note
    assert "crm.objects.deals.read" in note


def _records(provider: str) -> dict[str, dict[str, object]]:
    manifest = json.loads((FIXTURES / provider / "manifest.json").read_text())
    return {r["file"]: r for r in manifest["fixtures"]}


# Verifies: specs/lead-source-adapters/requirements.md#5.6
@pytest.mark.parametrize("file", ["deal_search.json", "no_open_deals/deal_search.json"])
def test_hubspot_deal_fixtures_flag_hs_is_closed_and_secondly_as_unverified(
    file: str,
) -> None:
    record = _records("hubspot")[file]
    note = str(record["note"])
    assert "hs_is_closed UNVERIFIED" in note
    assert "SECONDLY UNVERIFIED" in note


# Every field of these matched the provider's live docs on 2026-10-06 (full pages:
# docs.apollo.io OpenAPI, hunter.io/api-documentation/v2, serpapi.com/search-api and
# api-status-and-error-codes, HubSpot's search guide and spec repo).
VERIFIED = {
    ("apollo", "search.json"),
    ("apollo", "match.json"),
    ("apollo", "supported_technologies_excerpt.csv"),
    ("apollo", "no_match/search.json"),
    ("google_search", "search.json"),
    ("google_search", "no_results/search.json"),
    ("hubspot", "not_found/contact_search.json"),
    ("hunter", "domain_search.json"),
    ("hunter", "email_finder.json"),
    ("hunter", "email_verifier.json"),
    ("hunter", "no_emails/domain_search.json"),
    ("hunter", "invalid/email_verifier.json"),
    ("hunter", "accept_all/email_verifier.json"),
    ("hunter", "unknown/email_verifier.json"),
}


# Verifies: specs/lead-source-adapters/requirements.md#5.6
@pytest.mark.parametrize(("provider", "file"), sorted(VERIFIED))
def test_fixtures_whose_every_field_matched_the_docs_are_verified(
    provider: str, file: str
) -> None:
    record = _records(provider)[file]
    assert record["schema_status"] == "verified"
    assert record["schema_verified_on"] == "2026-10-06"
    assert str(record["doc_url"]).startswith("https://")


# Verifies: specs/lead-source-adapters/requirements.md#5.6
# Not verified: HubSpot's contact and deal property names (not in the docs read) and
# the hs_is_closed filter behind no_open_deals; Apollo's no-match answer and Hunter's
# finder "not found" answer, whose shapes no doc shows.
def test_no_other_fixture_claims_a_verification() -> None:
    for provider in ("apollo", "hubspot", "google_search", "hunter"):
        for file, record in _records(provider).items():
            if (provider, file) not in VERIFIED:
                assert record["schema_status"] == "unverified", file
                assert record["schema_verified_on"] is None, file
