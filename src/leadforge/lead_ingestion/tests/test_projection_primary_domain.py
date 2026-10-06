"""The projection reads the stored primary-domain tie resolution (16.11; 8.17, 8.18).

The real merge (``cluster_contributions`` then ``project_lead``) elects the display
primary domain of a Lead's company by trust-weighted vote over the sources naming it.
On an exact tie it reads the stored resolution and never calls a model: with none
stored the tie falls to the lowest-sorted candidate, flagged. The decision is recorded
on the result and in the merge log line, is order-independent, and Signal Strength
never decides it.
"""

import ast
import inspect
import itertools
import random
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from leadforge.lead_ingestion import projection
from leadforge.lead_ingestion.base_source import LeadContribution
from leadforge.lead_ingestion.clustering import cluster_contributions
from leadforge.lead_ingestion.database import create_store_engine
from leadforge.lead_ingestion.match_key_digest import MatchKeyDigester
from leadforge.lead_ingestion.merge_log import merge_log_events
from leadforge.lead_ingestion.models import (
    ConfidenceOrigin,
    DataMode,
    EmailStatus,
    FieldProvenance,
    TechSignal,
)
from leadforge.lead_ingestion.projection import ProjectionResult, project_lead
from leadforge.lead_ingestion.store.migrate import upgrade_to_head
from leadforge.lead_ingestion.store.tie_resolutions import TieResolutionRepository
from leadforge.lead_ingestion.tie_resolution import (
    TieResolutionRecord,
    TieSource,
    tie_key,
)

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
V = EmailStatus.VERIFIED
TIED = ("acme.com", "acme.io")
RANKS = {"a": 2, "b": 2, "c": 1}


def contribution(source: str, **values: Any) -> LeadContribution:
    mapped = {k.replace("__", "."): v for k, v in values.items()}
    return LeadContribution(
        source_name=source,
        values=mapped,
        provenance=tuple(
            FieldProvenance(
                canonical_path=path,
                source_name=source,
                data_mode=DataMode.SYNTHETIC,
                fetched_at=NOW,
                raw_field_path=f"raw.{path}",
                confidence_origin=ConfidenceOrigin.NONE,
                untrusted=False,
            )
            for path in mapped
        ),
    )


def person(source: str, **extra: Any) -> LeadContribution:
    return contribution(
        source, person__email="ann@acme.com", person__email_status=V, **extra
    )


def tied_pool() -> list[LeadContribution]:
    # Two equal-rank sources name both domains: an exact tie after the vote.
    return [
        person("a", company__domain=list(TIED), person__full_name="Ann Lee"),
        person("b", company__domain=list(TIED)),
    ]


class ReadOnlyStore:
    """A store the projection may read; any write is a failure."""

    def __init__(self, records: dict[str, TieResolutionRecord] | None = None) -> None:
        self.records = dict(records or {})

    def get(self, key: str) -> TieResolutionRecord | None:
        return self.records.get(key)

    def put(self, key: str, record: TieResolutionRecord) -> TieResolutionRecord:
        raise AssertionError("the projection must never write a resolution")


def stored(chosen: str) -> ReadOnlyStore:
    record = TieResolutionRecord(chosen, TIED, "fake-model", "p1", NOW)
    return ReadOnlyStore({tie_key(TIED, TIED): record})


def merge(
    pool: list[LeadContribution],
    store: ReadOnlyStore | None = None,
    ranks: dict[str, int] = RANKS,
) -> list[ProjectionResult]:
    return [
        project_lead(cl, ranks, tie_resolutions=store)
        for cl in cluster_contributions(pool)
    ]


def only(results: list[ProjectionResult]) -> ProjectionResult:
    assert len(results) == 1
    return results[0]


# Verifies: specs/lead-source-adapters/requirements.md#8.17
def test_a_single_domain_is_the_primary_domain_not_tied() -> None:
    result = only(merge([person("a", company__domain="acme.com")]))
    assert result.primary_domain == "acme.com"
    assert result.primary_domain_source is TieSource.NOT_TIED


# Verifies: specs/lead-source-adapters/requirements.md#8.17
def test_a_lead_with_no_company_domain_has_no_primary_domain() -> None:
    result = only(merge([person("a", company__name="Acme")]))
    assert result.primary_domain is None
    assert result.primary_domain_source is None


# Verifies: specs/lead-source-adapters/requirements.md#8.17
def test_the_trust_weighted_vote_breaks_what_would_otherwise_tie() -> None:
    # c also names acme.io: its weight decides, no tie remains.
    pool = [*tied_pool(), person("c", company__domain="acme.io")]
    result = only(merge(pool))
    assert result.primary_domain == "acme.io"
    assert result.primary_domain_source is TieSource.NOT_TIED


# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_an_unresolved_tie_falls_to_the_lowest_sorted_candidate_flagged() -> None:
    for store in (None, ReadOnlyStore()):
        result = only(merge(tied_pool(), store))
        assert result.primary_domain == "acme.com"
        assert result.primary_domain_source is TieSource.UNRESOLVED_PROVISIONAL
        assert result.primary_domain_flagged


# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_a_stored_resolution_decides_the_tie_without_a_write() -> None:
    result = only(merge(tied_pool(), stored("acme.io")))
    assert result.primary_domain == "acme.io"
    assert result.primary_domain_source is TieSource.STORED
    assert not result.primary_domain_flagged
    lead = result.lead
    assert lead is not None
    # Display only: the company's identity and domain set are unchanged.
    assert lead.employments[0].company.domains == TIED
    unresolved = only(merge(tied_pool())).lead
    assert unresolved is not None
    assert lead.employments == unresolved.employments


# Verifies: specs/lead-source-adapters/requirements.md#8.17
def test_the_primary_domain_changes_nothing_but_its_two_display_fields() -> None:
    pool = [*tied_pool(), person("c", company__name="Acme")]
    for chosen in TIED:
        decided = merge(pool, stored(chosen))
        undecided = merge(pool)
        assert [
            replace(r, primary_domain=None, primary_domain_source=None) for r in decided
        ] == [
            replace(r, primary_domain=None, primary_domain_source=None)
            for r in undecided
        ]
        # And the clusters are the same whatever the stored answer (no match rule).
        assert [r.contribution_count for r in decided] == [
            r.contribution_count for r in undecided
        ]


# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_a_resolution_stored_for_another_domain_set_is_not_read() -> None:
    record = TieResolutionRecord("acme.io", TIED, "fake-model", "p1", NOW)
    other = ReadOnlyStore({tie_key((*TIED, "acme.net"), TIED): record})
    result = only(merge(tied_pool(), other))
    assert result.primary_domain_source is TieSource.UNRESOLVED_PROVISIONAL


# Verifies: specs/lead-source-adapters/requirements.md#8.18 (property)
def test_the_decision_ignores_contribution_order() -> None:
    pool = [*tied_pool(), person("c", company__name="Acme")]
    rng = random.Random(1606)
    for store in (None, stored("acme.io")):
        expected = merge(pool, store)
        for order in itertools.permutations(pool):
            assert merge(list(order), store) == expected
        for _ in range(50):
            shuffled = pool[:]
            rng.shuffle(shuffled)
            assert merge(shuffled, store) == expected


# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_recomputing_is_byte_identical() -> None:
    store = stored("acme.io")
    assert repr(merge(tied_pool(), store)) == repr(merge(tied_pool(), store))
    assert merge(tied_pool(), store) == merge(tied_pool(), store)


# Verifies: specs/lead-source-adapters/requirements.md#24.4
def test_signal_strength_never_decides_the_primary_domain() -> None:
    outcomes = set()
    for strength_a, strength_b in ((0.1, 0.9), (0.9, 0.1), (0.5, 0.5)):
        pool = [
            person(
                "a",
                company__domain=list(TIED),
                company__signals=(TechSignal(label="aws", strength=strength_a),),
            ),
            person(
                "b",
                company__domain=list(TIED),
                company__signals=(TechSignal(label="gcp", strength=strength_b),),
            ),
        ]
        for store in (None, stored("acme.io")):
            r = only(merge(pool, store))
            outcomes.add((store is None, r.primary_domain, r.primary_domain_source))
    assert outcomes == {
        (True, "acme.com", TieSource.UNRESOLVED_PROVISIONAL),
        (False, "acme.io", TieSource.STORED),
    }


# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_the_primary_domain_is_never_in_repr() -> None:
    result = only(merge(tied_pool(), stored("acme.io")))
    assert "acme" not in repr(result)


# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_the_merge_log_records_how_a_tie_was_decided() -> None:
    digester = MatchKeyDigester(b"k" * 32, comparable_across_runs=False)
    unresolved = merge_log_events(merge(tied_pool()), digester=digester)
    resolved = merge_log_events(
        merge(tied_pool(), stored("acme.io")), digester=digester
    )
    plain_pool = [person("a", company__domain="acme.com"), person("b")]
    plain = merge_log_events(merge(plain_pool), digester=digester)
    assert unresolved[0].log_fields()["primary_domain_tie"] == "unresolved_provisional"
    assert resolved[0].log_fields()["primary_domain_tie"] == "stored"
    assert plain[0].log_fields()["primary_domain_tie"] is None
    for event in (*unresolved, *resolved):
        assert "acme" not in repr(event.log_fields())


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    url = f"sqlite:///{tmp_path / 'store.db'}"
    upgrade_to_head(url)
    eng = create_store_engine(url)
    yield eng
    eng.dispose()


# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_the_projection_reads_the_resolution_persisted_in_the_store(
    engine: Engine,
) -> None:
    with Session(engine) as session:
        TieResolutionRepository(session).put(
            tie_key(TIED, TIED),
            TieResolutionRecord("acme.io", TIED, "fake-model", "p1", NOW),
        )
        session.commit()
    with Session(engine) as session:
        repository = TieResolutionRepository(session)
        results = [
            project_lead(cl, RANKS, tie_resolutions=repository)
            for cl in cluster_contributions(tied_pool())
        ]
    result = only(results)
    assert result.primary_domain == "acme.io"
    assert result.primary_domain_source is TieSource.STORED


# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_the_projection_cannot_reach_the_model() -> None:
    # Only the read (``read_primary_domain_outcome``) is reachable: the escalation
    # (``resolve_primary_domain``), the resolver port and its ``choose`` never are.
    tree = ast.parse(Path(inspect.getfile(projection)).read_text(encoding="utf-8"))
    forbidden = {"resolve_primary_domain", "TieResolver", "choose"}
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names |= {
        alias.name
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom)
        for alias in n.names
    }
    assert not names & forbidden


# Verifies: specs/lead-source-adapters/requirements.md#8.17
def test_a_domain_of_another_company_never_becomes_the_primary_domain() -> None:
    # The top-ranked source names the Lead's company; two lower sources agree on a
    # different company. Their combined weight would win a vote, but a domain outside
    # the Employment's set is another company and casts nothing.
    ranks = {"a": 3, "b": 2, "c": 2, "d": 2}
    pool = [
        person("a", company__domain=list(TIED), person__full_name="Ann Lee"),
        *(person(name, company__domain=["other.com"]) for name in "bcd"),
    ]
    result = only(merge(pool, ranks=ranks))
    assert result.primary_domain in TIED
