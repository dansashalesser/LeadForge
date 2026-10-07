"""Constrained, persisted resolution of an exact primary-domain tie (16.11; 8.18)."""

import ast
import asyncio
import socket
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import structlog

from leadforge.lead_ingestion import tie_resolution
from leadforge.lead_ingestion.companies import (
    CompanyCluster,
    cluster_company_signals,
)
from leadforge.lead_ingestion.models import (
    CompanySignal,
    DataMode,
    ProviderCompanyId,
)
from leadforge.lead_ingestion.primary_domain import (
    PrimaryDomain,
    elect_primary_domain,
)
from leadforge.lead_ingestion.tie_resolution import (
    TIE_RESOLVER_TIMEOUT_SECONDS,
    TieAnswerRejectedError,
    TieOutcome,
    TieResolutionRecord,
    TieResolverError,
    TieSource,
    read_stored_primary_domain,
    resolve_primary_domain,
    tie_key,
    validate_tie_answer,
)

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
RANKS = {"one": 3, "two": 3}


def tied_cluster() -> CompanyCluster:
    """One company with a non-tied third domain and an exact a.com / b.com tie."""
    signals = [
        CompanySignal(
            company_id="v",
            name="Acme Secret Name",
            provider_ids=(ProviderCompanyId(source="one", id="1"),),
            domains=("a.com",),
        ),
        CompanySignal(
            company_id="v",
            name="Acme Secret Name",
            provider_ids=(ProviderCompanyId(source="two", id="2"),),
            domains=("b.com",),
        ),
        CompanySignal(
            company_id="v",
            name="Acme Secret Name",
            provider_ids=(ProviderCompanyId(source="three", id="3"),),
            domains=("a.com", "b.com", "c.com"),
        ),
    ]
    (only,) = cluster_company_signals(signals)
    return only


class FakeStore:
    def __init__(self) -> None:
        self.records: dict[str, TieResolutionRecord] = {}
        self.gets = 0
        self.puts = 0

    def get(self, key: str) -> TieResolutionRecord | None:
        self.gets += 1
        return self.records.get(key)

    def put(self, key: str, record: TieResolutionRecord) -> TieResolutionRecord:
        self.puts += 1
        return self.records.setdefault(key, record)


class FakeResolver:
    model = "fake-model"
    prompt_version = "p1"

    def __init__(self, answer: Any = "b.com", raises: BaseException | None = None):
        self.answer = answer
        self.raises = raises
        self.calls: list[tuple[tuple[str, ...], float]] = []

    def choose(self, candidates: tuple[str, ...], *, timeout: float) -> Any:
        self.calls.append((candidates, timeout))
        if self.raises is not None:
            raise self.raises
        return self.answer


class Factory:
    def __init__(self, resolver: FakeResolver) -> None:
        self.resolver = resolver
        self.built = 0

    def __call__(self) -> FakeResolver:
        self.built += 1
        return self.resolver


def primary_of(cluster: CompanyCluster) -> PrimaryDomain:
    p = elect_primary_domain(cluster, RANKS)
    assert p.tied, "fixture must be an exact tie"
    return p


def run(
    store: FakeStore,
    factory: Callable[[], FakeResolver] | None,
    mode: DataMode = DataMode.LIVE,
    cluster: CompanyCluster | None = None,
) -> TieOutcome:
    cluster = cluster or tied_cluster()
    return resolve_primary_domain(
        cluster,
        primary_of(cluster),
        mode=mode,
        store=store,
        resolver_factory=factory,
        now=NOW,
    )


# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_a_valid_answer_becomes_the_primary_domain_and_is_persisted() -> None:
    store, resolver = FakeStore(), FakeResolver("b.com")
    out = run(store, Factory(resolver))
    assert out.domain == "b.com"
    assert out.source is TieSource.RESOLVED
    assert not out.flagged
    (record,) = store.records.values()
    assert record.chosen_domain == "b.com"
    assert record.candidates == ("a.com", "b.com")
    assert (record.model, record.prompt_version) == ("fake-model", "p1")
    assert record.resolved_at == NOW


# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_the_model_sees_only_the_tied_candidates_with_a_bounded_timeout() -> None:
    resolver = FakeResolver("a.com")
    run(FakeStore(), Factory(resolver))
    # c.com belongs to the company but is not tied; no name, id or signal is sent.
    assert resolver.calls == [(("a.com", "b.com"), TIE_RESOLVER_TIMEOUT_SECONDS)]
    assert 0 < TIE_RESOLVER_TIMEOUT_SECONDS <= 60


# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_a_stored_resolution_is_read_and_the_model_is_never_built_again() -> None:
    store, resolver = FakeStore(), FakeResolver("b.com")
    factory = Factory(resolver)
    first = run(store, factory)
    second = run(store, factory)
    assert (second.domain, second.source) == ("b.com", TieSource.STORED)
    assert first.domain == second.domain
    assert factory.built == 1
    assert len(resolver.calls) == 1
    assert store.puts == 1


# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_the_projection_read_takes_no_resolver_and_is_a_pure_lookup() -> None:
    cluster = tied_cluster()
    primary = primary_of(cluster)
    store = FakeStore()
    assert read_stored_primary_domain(cluster, primary, store) == primary.domain
    run(store, Factory(FakeResolver("b.com")))
    reads = {read_stored_primary_domain(cluster, primary, store) for _ in range(3)}
    assert reads == {"b.com"}
    assert store.puts == 1  # reading never writes


# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_an_untied_primary_needs_no_resolver_and_touches_no_store() -> None:
    signals = [
        CompanySignal(
            company_id="v",
            name="N",
            provider_ids=(ProviderCompanyId(source="one", id="1"),),
            domains=("a.com",),
        )
    ]
    (cluster,) = cluster_company_signals(signals)
    primary = elect_primary_domain(cluster, RANKS)
    store, factory = FakeStore(), Factory(FakeResolver())
    out = resolve_primary_domain(
        cluster,
        primary,
        mode=DataMode.LIVE,
        store=store,
        resolver_factory=factory,
        now=NOW,
    )
    assert (out.domain, out.source) == ("a.com", TieSource.NOT_TIED)
    assert (factory.built, store.gets, store.puts) == (0, 0, 0)
    assert read_stored_primary_domain(cluster, primary, store) == "a.com"


# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_synthetic_mode_never_builds_or_calls_the_model_and_flags_the_tie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*a: object, **k: object) -> None:
        raise AssertionError("a synthetic tie opened a socket")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    store, resolver = FakeStore(), FakeResolver("b.com")
    factory = Factory(resolver)
    out = run(store, factory, DataMode.SYNTHETIC)
    assert out.domain == "a.com"  # lowest-sorted candidate
    assert out.source is TieSource.SYNTHETIC_PROVISIONAL
    assert out.flagged
    assert (factory.built, len(resolver.calls), store.puts) == (0, 0, 0)


# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_a_synthetic_recompute_still_reads_a_stored_resolution() -> None:
    store = FakeStore()
    run(store, Factory(FakeResolver("b.com")))
    factory = Factory(FakeResolver("a.com"))
    out = run(store, factory, DataMode.SYNTHETIC)
    assert (out.domain, out.source) == ("b.com", TieSource.STORED)
    assert factory.built == 0


@pytest.mark.parametrize(
    "answer",
    [
        "evil.com",
        "",
        "   ",
        " a.com",
        "a.com ",
        "A.COM",
        "www.a.com",
        None,
        7,
        ["a.com"],
    ],
)
# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_an_answer_outside_the_candidate_set_is_rejected_and_never_persisted(
    answer: Any,
) -> None:
    store = FakeStore()
    with structlog.testing.capture_logs() as logs:
        out = run(store, Factory(FakeResolver(answer)))
    assert out.domain == "a.com"  # the deterministic provisional winner
    assert out.source is TieSource.REJECTED_PROVISIONAL
    assert out.flagged
    assert store.puts == 0
    event = next(e for e in logs if e["event"] == "primary_domain_tie_resolution")
    assert event["log_level"] == "warning"
    assert event["outcome"] == "rejected_provisional"
    assert event["candidates"] == 2
    assert "evil.com" not in repr(logs)
    assert "a.com" not in repr(logs)


# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_validate_tie_answer_is_exact_and_names_its_error() -> None:
    assert validate_tie_answer("b.com", ("a.com", "b.com")) == "b.com"
    with pytest.raises(TieAnswerRejectedError) as info:
        validate_tie_answer("evil.com", ("a.com", "b.com"))
    assert "evil.com" not in str(info.value)
    assert "evil.com" not in repr(info.value)
    assert "a.com" not in str(info.value)


@pytest.mark.parametrize(
    "error", [TieResolverError("down"), TimeoutError("slow")], ids=["port", "timeout"]
)
# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_a_resolver_failure_falls_back_flagged_and_is_not_persisted(
    error: BaseException,
) -> None:
    store = FakeStore()
    resolver = FakeResolver(raises=error)
    out = run(store, Factory(resolver))
    assert (out.domain, out.source) == ("a.com", TieSource.UNAVAILABLE_PROVISIONAL)
    assert out.flagged
    assert store.puts == 0
    assert len(resolver.calls) == 1  # no retry here


# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_other_errors_and_cancellation_are_not_swallowed() -> None:
    with pytest.raises(ZeroDivisionError):
        run(FakeStore(), Factory(FakeResolver(raises=ZeroDivisionError())))
    with pytest.raises(asyncio.CancelledError):
        run(FakeStore(), Factory(FakeResolver(raises=asyncio.CancelledError())))


# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_live_mode_without_a_configured_resolver_falls_back_flagged() -> None:
    store = FakeStore()
    out = run(store, None)
    assert (out.domain, out.source) == ("a.com", TieSource.NO_RESOLVER_PROVISIONAL)
    assert out.flagged
    assert store.puts == 0


# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_a_lost_race_adopts_the_stored_winner_for_idempotence() -> None:
    class RacingStore(FakeStore):
        def get(self, key: str) -> TieResolutionRecord | None:
            self.gets += 1
            return None  # not yet visible when we looked

    store = RacingStore()
    cluster = tied_cluster()
    key = tie_key(cluster.domains, ("a.com", "b.com"))
    store.records[key] = TieResolutionRecord(
        "a.com", ("a.com", "b.com"), "other", "p0", NOW
    )
    out = run(store, Factory(FakeResolver("b.com")))
    assert (out.domain, out.source) == ("a.com", TieSource.STORED)


# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_the_tie_key_is_the_domain_set_and_candidate_set_not_a_cluster_id() -> None:
    k = tie_key(("a.com", "b.com", "c.com"), ("a.com", "b.com"))
    assert k == tie_key(("c.com", "a.com", "b.com"), ("b.com", "a.com"))
    assert k != tie_key(("a.com", "b.com", "c.com", "d.com"), ("a.com", "b.com"))
    assert k != tie_key(("a.com", "b.com", "c.com"), ("a.com", "c.com"))
    assert len(k) == 64
    assert "a.com" not in k


# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_a_record_must_choose_from_its_candidates_and_hides_domains_from_repr() -> None:
    with pytest.raises(ValueError, match="candidates"):
        TieResolutionRecord("z.com", ("a.com", "b.com"), "m", "p", NOW)
    with pytest.raises(ValueError, match="model"):
        TieResolutionRecord("a.com", ("a.com", "b.com"), " ", "p", NOW)
    with pytest.raises(ValueError, match="timezone"):
        TieResolutionRecord("a.com", ("a.com", "b.com"), "m", "p", datetime(2026, 1, 1))
    record = TieResolutionRecord("a.com", ("a.com", "b.com"), "m", "p", NOW)
    assert "a.com" not in repr(record)
    assert "b.com" not in repr(record)
    assert "a.com" not in repr(TieOutcome("a.com", TieSource.RESOLVED))


# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_the_merge_code_imports_no_model_client_and_no_store() -> None:
    tree = ast.parse(Path(tie_resolution.__file__).read_text())
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module)
    banned = ("langchain", "openai", "anthropic", "httpx", "requests", "sqlalchemy")
    assert not [r for r in roots if r.startswith(banned) or ".store" in r]
