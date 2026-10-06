"""The projection version: a rule change is observable, never silent (16.6, 8.13).

``projection_version`` increments whenever what the projection is a function of
changes: the projection rules themselves (``PROJECTION_RULES_REVISION``), the Identity
Exclusions (8.13) or the Source Trust Ranking (design, "Temporal aspects"). A stored
projection carrying an older version is flagged stale and recomputed from the
contribution log with no contribution record mutated or deleted.
"""

import itertools
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.base_source import LeadContribution
from leadforge.lead_ingestion.clustering import cluster_contributions
from leadforge.lead_ingestion.database import create_store_engine
from leadforge.lead_ingestion.match_key_digest import (
    MATCH_KEY_SECRET_ENV,
    MatchKeyDigester,
)
from leadforge.lead_ingestion.match_keys import IdentityExclusions
from leadforge.lead_ingestion.models import (
    ConfidenceOrigin,
    DataMode,
    EmailStatus,
    FieldProvenance,
)
from leadforge.lead_ingestion.projection import (
    PROJECTION_RULES_REVISION,
    ProjectionBasis,
    ProjectionStamp,
    project_lead,
    stamp_projection,
)
from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.store.merged_leads import (
    SourceBatch,
    persist_merge,
    stale_projections,
)
from leadforge.lead_ingestion.store.migrate import upgrade_to_head
from leadforge.lead_ingestion.store.raw_responses import RetentionPolicy

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
V = EmailStatus.VERIFIED
NONE = IdentityExclusions()
RANKS = {"a": 1, "c": 3}
KEY = MatchKeyDigester(b"k" * 32, comparable_across_runs=True)


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


def over_merged_pool() -> list[LeadContribution]:
    # Ann's LinkedIn record and a bare record share info@x.com: one cluster until
    # the shared address is excluded (as in test_identity_exclusions).
    return [
        contribution(
            "a",
            person__linkedin_url="https://linkedin.com/in/ann",
            person__email="info@x.com",
            person__email_status=V,
        ),
        contribution("c", person__email="info@x.com", person__email_status=V),
    ]


# Verifies: specs/lead-source-adapters/requirements.md#8.13
def test_the_rules_revision_is_bumped_for_the_rule_changes_since_16_5() -> None:
    # 1 = the 16.5 rules; 2 = one-sided email (8.3), request-echo fields counted only
    # when no other source has the field, LinkedIn cannot-link, the confidence table
    # changes and the primary domain read from the stored tie resolution (16.11).
    assert PROJECTION_RULES_REVISION == 2
    assert ProjectionBasis.of(NONE, RANKS).rules_revision == 2


# Verifies: specs/lead-source-adapters/requirements.md#8.13
def test_a_first_projection_is_version_one() -> None:
    assert stamp_projection(None, ProjectionBasis.of(NONE, RANKS)).version == 1


# Verifies: specs/lead-source-adapters/requirements.md#8.13
def test_an_unchanged_basis_keeps_the_version() -> None:
    basis = ProjectionBasis.of(NONE, RANKS)
    first = stamp_projection(None, basis)
    assert stamp_projection(first, basis) == first
    assert stamp_projection(first, ProjectionBasis.of(NONE, dict(RANKS))) == first
    assert not first.is_stale_for(basis)


# Verifies: specs/lead-source-adapters/requirements.md#8.13
def test_changing_the_exclusion_set_bumps_the_version() -> None:
    first = stamp_projection(None, ProjectionBasis.of(NONE, RANKS))
    barred = ProjectionBasis.of(
        IdentityExclusions.from_values(emails=["info@x.com"]), RANKS, digester=KEY
    )
    second = stamp_projection(first, barred)
    assert second.version == 2
    assert first.is_stale_for(barred)
    assert not second.is_stale_for(barred)
    # Removing the entry again is another change, not a return to version 1.
    third = stamp_projection(second, ProjectionBasis.of(NONE, RANKS))
    assert third.version == 3


# Verifies: specs/lead-source-adapters/requirements.md#8.13
def test_changing_the_trust_ranking_bumps_the_version() -> None:
    first = stamp_projection(None, ProjectionBasis.of(NONE, RANKS))
    second = stamp_projection(first, ProjectionBasis.of(NONE, {"a": 3, "c": 1}))
    assert second.version == 2


# Verifies: specs/lead-source-adapters/requirements.md#8.13
def test_changing_the_rules_revision_bumps_the_version() -> None:
    old = ProjectionBasis.of(NONE, RANKS, rules_revision=1)
    first = stamp_projection(None, old)
    second = stamp_projection(first, ProjectionBasis.of(NONE, RANKS))
    assert second.version == 2
    assert first.is_stale_for(ProjectionBasis.of(NONE, RANKS))


# Verifies: specs/lead-source-adapters/requirements.md#8.13 (property)
def test_the_fingerprint_ignores_input_order_and_separates_every_distinct_set() -> None:
    values = ["info@x.com", "sales@y.com", "ops@z.com"]
    seen: dict[frozenset[str], str] = {}
    for size in range(len(values) + 1):
        for subset in itertools.combinations(values, size):
            prints = {
                ProjectionBasis.of(
                    IdentityExclusions.from_values(emails=list(order)),
                    RANKS,
                    digester=KEY,
                ).fingerprint
                for order in itertools.permutations(subset)
            }
            assert len(prints) == 1
            seen[frozenset(subset)] = prints.pop()
    assert len(set(seen.values())) == len(seen)


# Verifies: specs/lead-source-adapters/requirements.md#8.13
def test_the_basis_and_stamp_never_show_exclusion_values_or_digests() -> None:
    basis = ProjectionBasis.of(
        IdentityExclusions.from_values(emails=["info@x.com"]), RANKS, digester=KEY
    )
    stamp = stamp_projection(None, basis)
    for text in (repr(basis), repr(stamp)):
        assert "info@x.com" not in text
        assert basis.fingerprint not in text
        assert basis.exclusions_token not in text


# Verifies: specs/lead-source-adapters/requirements.md#8.13 (21.4: no plain hash)
def test_the_exclusion_part_is_keyed_never_a_plain_hash_of_the_values() -> None:
    # A plain sha256 of a low-entropy email is reversible by dictionary attack, and
    # the fingerprint is meant to be stored: the exclusions enter it only as keyed
    # HMAC digests (match_key_digest), so without the secret nothing can be guessed.
    exclusions = IdentityExclusions.from_values(emails=["info@x.com"])
    keyed = ProjectionBasis.of(exclusions, RANKS, digester=KEY)
    other = MatchKeyDigester(b"q" * 32, comparable_across_runs=True)
    assert keyed.exclusions_token != exclusions.version_token
    assert keyed.fingerprint != ProjectionBasis.of(NONE, RANKS).fingerprint
    assert (
        keyed.fingerprint
        != ProjectionBasis.of(exclusions, RANKS, digester=other).fingerprint
    )
    # Same key, same set: stable across calls (a stored fingerprint stays valid).
    again = MatchKeyDigester(b"k" * 32, comparable_across_runs=True)
    assert (
        keyed.fingerprint
        == ProjectionBasis.of(exclusions, RANKS, digester=again).fingerprint
    )


# Verifies: specs/lead-source-adapters/requirements.md#8.13
@pytest.mark.parametrize(
    "digester",
    [None, MatchKeyDigester(b"r" * 32, comparable_across_runs=False)],
    ids=["no-digester", "per-run-random-key"],
)
def test_exclusions_without_a_stable_secret_are_refused(
    digester: MatchKeyDigester | None,
) -> None:
    # A per-run key would change the fingerprint every run: every Lead stale, every
    # run, silently. Fail closed and name the variable, never the value.
    exclusions = IdentityExclusions.from_values(emails=["info@x.com"])
    with pytest.raises(ValueError, match=MATCH_KEY_SECRET_ENV) as caught:
        ProjectionBasis.of(exclusions, RANKS, digester=digester)
    assert "info@x.com" not in str(caught.value)
    # No exclusions: nothing personal to key, so no secret is needed.
    assert ProjectionBasis.of(NONE, RANKS, digester=digester).fingerprint == (
        ProjectionBasis.of(NONE, RANKS).fingerprint
    )


# Verifies: specs/lead-source-adapters/requirements.md#8.13
def test_a_version_below_one_is_refused() -> None:
    basis = ProjectionBasis.of(NONE, RANKS)
    with pytest.raises(ValueError, match="version"):
        ProjectionStamp(0, basis.fingerprint)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    url = f"sqlite:///{tmp_path / 'store.db'}"
    upgrade_to_head(url)
    eng = create_store_engine(url)
    yield eng
    eng.dispose()


def _start_run(session: Session) -> uuid.UUID:
    run = m.IngestionRun(started_at=NOW, status="running")
    session.add(run)
    session.flush()
    for name in ("a", "c"):
        session.add(
            m.SourceRun(run_id=run.id, source_name=name, resolved_mode="synthetic")
        )
    session.flush()
    return run.id


def _contribution_rows(session: Session) -> list[tuple[Any, ...]]:
    rows = session.execute(
        sa.select(
            m.ContributionField.id,
            m.ContributionField.contribution_id,
            m.ContributionField.canonical_path,
            m.ContributionField.value,
        ).order_by(m.ContributionField.id)
    )
    return [tuple(r) for r in rows]


# Verifies: specs/lead-source-adapters/requirements.md#8.13
def test_a_stored_projection_of_an_older_version_is_flagged_and_recomputed(
    engine: Engine,
) -> None:
    pool = over_merged_pool()
    stored_stamp = stamp_projection(None, ProjectionBasis.of(NONE, RANKS))
    with Session(engine) as session:
        run_id = _start_run(session)
        batches = [
            SourceBatch("a", DataMode.SYNTHETIC, "discovery", {"r": "a"}, (pool[0],)),
            SourceBatch("c", DataMode.SYNTHETIC, "discovery", {"r": "c"}, (pool[1],)),
        ]
        merged = [(cl, project_lead(cl, RANKS)) for cl in cluster_contributions(pool)]
        assert sum(1 for _, r in merged if r.lead is not None) == 1  # over-merged
        persist_merge(
            session,
            run_id=run_id,
            batches=batches,
            merged=merged,
            computed_at=NOW,
            projection_version=stored_stamp.version,
            retention=RetentionPolicy(),
        )
        session.commit()
        before = _contribution_rows(session)
        assert stale_projections(session, current_version=stored_stamp.version) == ()

        # The operator bars the shared address: the version bumps ...
        exclusions = IdentityExclusions.from_values(emails=["info@x.com"])
        current = stamp_projection(
            stored_stamp, ProjectionBasis.of(exclusions, RANKS, digester=KEY)
        )
        assert current.version == stored_stamp.version + 1

        # ... so the stored projection is flagged ...
        identity = session.scalar(sa.select(m.CanonicalLeadRow.lead_identity_id))
        assert stale_projections(session, current_version=current.version) == (
            identity,
        )

        # ... and recomputing the merge from the same contributions separates it.
        recomputed = [
            project_lead(cl, RANKS).lead
            for cl in cluster_contributions(pool, exclusions)
        ]
        assert sum(1 for lead in recomputed if lead is not None) == 2
        assert _contribution_rows(session) == before  # nothing mutated or deleted


# Verifies: specs/lead-source-adapters/requirements.md#8.13
def test_stale_projections_refuses_a_version_below_one(engine: Engine) -> None:
    with Session(engine) as session, pytest.raises(ValueError, match="version"):
        stale_projections(session, current_version=0)
