"""Re-merging the contribution log without duplicate Leads (follow-up, user option A).

Contributions stay append-only (8.12); which Lead a contribution belongs to is a DERIVED
mapping (``contribution_lead``) rebuilt by every merge. One code path saves a first
merge and every re-merge: contributions are inserted once (keyed by their content
identity), the mapping is rebuilt, Leads keep a stable identity and a Lead no longer
produced is retired with pointers to its successors, never deleted and never left
active beside them. Every store test runs on SQLite and on PostgreSQL.
"""

# ruff: noqa: F811 - fixtures imported from test_persistence_both_engines

import random
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.base_source import LeadContribution
from leadforge.lead_ingestion.clustering import canonical_json
from leadforge.lead_ingestion.match_keys import IdentityExclusions
from leadforge.lead_ingestion.models import (
    AbsenceKind,
    ConfidenceOrigin,
    DataMode,
    FieldProvenance,
    SourceAbsence,
    UntrustedText,
)
from leadforge.lead_ingestion.remerge import merge_contributions, project_with_store
from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.store.contributions import (
    contribution_sha,
    load_lead_contributions,
    write_contribution,
)
from leadforge.lead_ingestion.store.merged_leads import (
    MergeStored,
    SourceBatch,
    assign_leads,
    persist_merge,
    stale_projections,
)
from leadforge.lead_ingestion.store.raw_responses import (
    RawResponseRepository,
    RetentionPolicy,
)
from leadforge.lead_ingestion.tests.test_persistence_both_engines import (  # noqa: F401 - fixtures
    Backend,
    _alembic,
    backend,
    blank,
    postgres_url,
)
from leadforge.lead_ingestion.tie_resolution import TieSource

NOW = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)
RANKS = {"alpha": 2, "beta": 1, "gamma": 3}
NONE = IdentityExclusions()
V = "verified"


def contribution(
    source: str,
    *,
    at: datetime = NOW,
    mode: DataMode = DataMode.SYNTHETIC,
    **values: Any,
) -> LeadContribution:
    """A contribution from canonical path (``__`` for ``.``) to value."""
    paths = {k.replace("__", "."): v for k, v in values.items()}
    return LeadContribution(
        source_name=source,
        values=paths,
        provenance=tuple(
            FieldProvenance(
                canonical_path=path,
                source_name=source,
                data_mode=mode,
                fetched_at=at,
                raw_field_path=f"raw.{path}",
                confidence_origin=ConfidenceOrigin.NONE,
                untrusted=False,
            )
            for path in paths
        ),
    )


def ada(source: str, *, at: datetime = NOW, **extra: Any) -> LeadContribution:
    return contribution(
        source,
        at=at,
        person__email="ada@example.com",
        person__email_status=V,
        person__full_name="Ada Lovelace",
        **extra,
    )


def start_run(session: Session, *sources: str) -> uuid.UUID:
    run = m.IngestionRun(started_at=NOW, status="running")
    session.add(run)
    session.flush()
    for name in sources or tuple(RANKS):
        session.add(
            m.SourceRun(run_id=run.id, source_name=name, resolved_mode="synthetic")
        )
    session.flush()
    return run.id


def batches_of(*contributions: LeadContribution) -> tuple[SourceBatch, ...]:
    by_source: dict[str, list[LeadContribution]] = {}
    for c in contributions:
        by_source.setdefault(c.source_name, []).append(c)
    return tuple(
        SourceBatch(name, DataMode.SYNTHETIC, "discovery", {"raw": name}, tuple(cs))
        for name, cs in sorted(by_source.items())
    )


def remerge(
    engine: Engine,
    contributions: Sequence[LeadContribution] = (),
    *,
    exclusions: IdentityExclusions = NONE,
    at: datetime = NOW,
) -> MergeStored:
    """One run: the new contributions (if any) merged with everything stored."""
    with Session(engine) as session, session.begin():
        run_id = start_run(session)
        merged = project_with_store(
            session, contributions, exclusions=exclusions, trust_ranks=RANKS, now=at
        )
        return persist_merge(
            session,
            run_id=run_id,
            batches=batches_of(*contributions),
            merged=merged,
            computed_at=at,
            projection_version=1,
            projection_fingerprint="f" * 64,
            retention=RetentionPolicy(),
        )


def active_leads(engine: Engine) -> set[uuid.UUID]:
    with Session(engine) as s:
        return set(
            s.scalars(
                sa.select(m.CanonicalLeadRow.lead_identity_id)
                .join(
                    m.LeadIdentity,
                    m.LeadIdentity.id == m.CanonicalLeadRow.lead_identity_id,
                )
                .where(m.LeadIdentity.retired_at.is_(None))
            )
        )


def retired(engine: Engine) -> dict[uuid.UUID, set[uuid.UUID]]:
    with Session(engine) as s:
        out: dict[uuid.UUID, set[uuid.UUID]] = {
            i: set()
            for i in s.scalars(
                sa.select(m.LeadIdentity.id).where(
                    m.LeadIdentity.retired_at.is_not(None)
                )
            )
        }
        for before, after in s.execute(
            sa.select(m.LeadSuccession.predecessor_id, m.LeadSuccession.successor_id)
        ):
            out.setdefault(before, set()).add(after)
        return out


def mapping(engine: Engine) -> dict[uuid.UUID, uuid.UUID]:
    with Session(engine) as s:
        return {
            c: lead
            for c, lead in s.execute(
                sa.select(
                    m.ContributionLead.contribution_id,
                    m.ContributionLead.lead_identity_id,
                )
            )
        }


def contribution_rows(engine: Engine) -> list[tuple[Any, ...]]:
    """Every column of every append-only contribution row, in a stable order."""
    out: list[tuple[Any, ...]] = []
    with Session(engine) as s:
        for model in (m.SourceContribution, m.ContributionField, m.ContributionAbsence):
            columns = [c for c in model.__table__.columns]
            out.extend(
                tuple(r)
                for r in s.execute(sa.select(*columns).order_by(model.__table__.c.id))
            )
    return out


def lead_of(engine: Engine, email: str) -> set[uuid.UUID]:
    with Session(engine) as s:
        return set(
            s.scalars(
                sa.select(m.CanonicalLeadRow.lead_identity_id)
                .join(
                    m.LeadIdentity,
                    m.LeadIdentity.id == m.CanonicalLeadRow.lead_identity_id,
                )
                .where(
                    m.LeadIdentity.retired_at.is_(None),
                    m.CanonicalLeadRow.email == email,
                )
            )
        )


# ------------------------------------------------------------ the pure assignment rule


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_a_cluster_whose_contributions_all_belong_to_one_lead_keeps_it() -> None:
    plan = assign_leads([{"a", "b", "new"}], {"a": "L", "b": "L"}, seniority=str)
    assert plan.kept == ("L",)
    assert plan.retired == {}


# Verifies: specs/lead-source-adapters/requirements.md#8.13
def test_a_split_lead_is_retired_and_every_part_gets_a_new_lead() -> None:
    plan = assign_leads([{"a"}, {"b"}], {"a": "L", "b": "L"}, seniority=str)
    assert plan.kept == (None, None)
    assert plan.retired == {"L": (0, 1)}


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_a_join_keeps_the_most_senior_lead_and_retires_the_absorbed_one() -> None:
    plan = assign_leads(
        [{"a", "b"}], {"a": "L2", "b": "L1"}, seniority=lambda lead: lead
    )
    assert plan.kept == ("L1",)
    assert plan.retired == {"L2": (0,)}


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_a_lead_that_may_not_continue_is_retired_into_its_cluster() -> None:
    plan = assign_leads(
        [{"a"}], {"a": "L"}, seniority=str, can_continue=lambda lead, i: False
    )
    assert plan.kept == (None,)
    assert plan.retired == {"L": (0,)}


# Verifies: specs/lead-source-adapters/requirements.md#8.12 (property)
def test_assignment_never_gives_one_lead_to_two_clusters_over_random_inputs() -> None:
    rng = random.Random(1606)
    for _ in range(500):
        contributions = [f"c{i}" for i in range(rng.randint(1, 8))]
        rng.shuffle(contributions)
        clusters: list[set[str]] = []
        for c in contributions:
            if clusters and rng.random() < 0.5:
                rng.choice(clusters).add(c)
            else:
                clusters.append({c})
        prior = {
            c: f"L{rng.randint(0, 3)}" for c in contributions if rng.random() < 0.7
        }
        plan = assign_leads(clusters, prior, seniority=str)
        kept = [lead for lead in plan.kept if lead is not None]
        assert len(kept) == len(set(kept))
        touched = set(prior.values())
        # Every prior lead touched either continues once or is retired, never both.
        assert set(kept) | set(plan.retired) == touched
        assert not set(kept) & set(plan.retired)
        for lead, successors in plan.retired.items():
            assert successors == tuple(
                sorted(
                    i
                    for i, cl in enumerate(clusters)
                    if any(prior.get(c) == lead for c in cl)
                )
            )


# ---------------------------------------------------------- the stored contribution log


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_a_stored_contribution_reads_back_with_the_same_identity_and_content(
    backend: Backend,
) -> None:
    text = UntrustedText(value="bio <b>", truncated=False, original_length=7)
    original = LeadContribution(
        source_name="alpha",
        values={
            "person.email": "ada@example.com",
            "person.bio": text,
            "company.founded": datetime(1840, 1, 1, tzinfo=UTC),
            "company.domains": ("a.com", "b.com"),
            "person.score": 0.5,
        },
        provenance=tuple(
            FieldProvenance(
                canonical_path=path,
                source_name="alpha",
                data_mode=DataMode.LIVE,
                fetched_at=NOW,
                raw_field_path=f"raw.{path}",
                untrusted=path == "person.bio",
                **(
                    {
                        "confidence_origin": ConfidenceOrigin.PROVIDER_STATED,
                        "confidence": 0.9,
                        "confidence_raw": "90",
                        "confidence_scale": "percent",
                    }
                    if path == "person.email"
                    else {
                        "confidence_origin": ConfidenceOrigin.HEURISTIC,
                        "confidence": 0.4,
                    }
                    if path == "person.score"
                    else {"confidence_origin": ConfidenceOrigin.NONE}
                ),
            )
            for path in (
                "person.email",
                "person.bio",
                "company.founded",
                "company.domains",
                "person.score",
            )
        ),
        absences=(
            SourceAbsence(
                canonical_path="person.phone",
                source_name="alpha",
                kind=AbsenceKind.NEGATIVE_EVIDENCE,
                raw_field_path="raw.phone",
            ),
            SourceAbsence(
                canonical_path="person.twitter",
                source_name="alpha",
                kind=AbsenceKind.NOT_APPLICABLE,
            ),
        ),
    )
    with Session(backend.engine) as s, s.begin():
        run_id = start_run(s, "alpha")
        source_run = s.scalar(
            sa.select(m.SourceRun.id).where(m.SourceRun.run_id == run_id)
        )
        assert source_run is not None
        raw = RawResponseRepository.add(
            s,
            source_run_id=source_run,
            endpoint_key="people",
            request_fingerprint="fp",
            payload={},
            fetched_at=NOW,
            mode=DataMode.LIVE,
            policy=RetentionPolicy(),
        )
        write_contribution(
            s,
            original,
            source_run_id=source_run,
            raw_response_id=raw,
            data_mode=DataMode.LIVE,
            fetched_at=NOW,
            lead_scope="person",
        )
    with Session(backend.engine) as s:
        (back,) = load_lead_contributions(s).values()
    assert canonical_json(back) == canonical_json(original)
    assert contribution_sha(back) == contribution_sha(original)
    assert isinstance(back.values["person.bio"], UntrustedText)


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_the_contribution_identity_ignores_when_it_was_fetched() -> None:
    assert contribution_sha(ada("alpha")) == contribution_sha(
        ada("alpha", at=NOW + timedelta(days=3))
    )
    assert contribution_sha(ada("alpha")) != contribution_sha(ada("beta"))
    assert contribution_sha(ada("alpha")) != contribution_sha(
        ada("alpha", person__title="CTO")
    )


# --------------------------------------------------------------- re-merge, both engines


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_remerging_the_same_contributions_twice_keeps_lead_count_and_ids(
    backend: Backend,
) -> None:
    first = remerge(
        backend.engine,
        [
            ada("alpha"),
            ada("beta"),
            contribution(
                "gamma",
                person__linkedin_url="https://www.linkedin.com/in/bob",
                person__full_name="Bob",
            ),
        ],
    )
    leads, links, rows = (
        active_leads(backend.engine),
        mapping(backend.engine),
        contribution_rows(backend.engine),
    )
    again = remerge(backend.engine)  # nothing new: the stored log, merged again

    assert (first.canonical_leads, first.leads_created, first.contributions) == (
        2,
        2,
        3,
    )
    assert (again.canonical_leads, again.leads_created, again.leads_retired) == (
        2,
        0,
        0,
    )
    assert again.contributions == 0
    assert active_leads(backend.engine) == leads
    assert mapping(backend.engine) == links
    assert contribution_rows(backend.engine) == rows
    assert retired(backend.engine) == {}


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_repeated_ingest_of_the_same_data_adds_no_lead_and_no_contribution(
    backend: Backend,
) -> None:
    remerge(backend.engine, [ada("alpha"), ada("beta")])
    leads, rows = active_leads(backend.engine), contribution_rows(backend.engine)
    later = NOW + timedelta(days=1)
    again = remerge(
        backend.engine, [ada("alpha", at=later), ada("beta", at=later)], at=later
    )

    assert again.contributions == 0
    assert active_leads(backend.engine) == leads
    assert contribution_rows(backend.engine) == rows
    with Session(backend.engine) as s:
        assert s.scalar(sa.select(sa.func.count()).select_from(m.CanonicalLeadRow)) == 1
        assert s.scalar(sa.select(sa.func.count()).select_from(m.LeadIdentity)) == 1


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_new_data_for_a_known_person_joins_their_existing_lead(
    backend: Backend,
) -> None:
    remerge(backend.engine, [ada("alpha")])
    (lead,) = active_leads(backend.engine)
    more = remerge(
        backend.engine,
        [ada("gamma", person__title="Countess")],
        at=NOW + timedelta(hours=1),
    )

    assert (more.contributions, more.leads_created, more.leads_retired) == (1, 0, 0)
    assert active_leads(backend.engine) == {lead}
    with Session(backend.engine) as s:
        row = s.scalars(sa.select(m.CanonicalLeadRow)).one()
        assert row.contributing_sources == ["alpha", "gamma"]
    assert set(mapping(backend.engine).values()) == {lead}


# Verifies: specs/lead-source-adapters/requirements.md#8.13
def test_a_split_by_a_new_exclusion_retires_the_lead_with_pointers_to_the_new_ones(
    backend: Backend,
) -> None:
    remerge(backend.engine, [ada("alpha"), ada("beta", person__title="Countess")])
    (old,) = active_leads(backend.engine)
    rows = contribution_rows(backend.engine)

    bar = IdentityExclusions.from_values(emails=["ada@example.com"])
    split = remerge(backend.engine, exclusions=bar, at=NOW + timedelta(hours=1))

    new = active_leads(backend.engine)
    assert (split.leads_created, split.leads_retired, split.canonical_leads) == (
        2,
        1,
        2,
    )
    assert len(new) == 2
    assert old not in new
    assert retired(backend.engine) == {old: new}
    assert contribution_rows(backend.engine) == rows  # no contribution mutated
    assert set(mapping(backend.engine).values()) == new
    assert len(set(mapping(backend.engine))) == 2
    with Session(backend.engine) as s:
        # The retired projection is kept (never deleted), but is no longer active.
        assert (
            s.get(
                m.CanonicalLeadRow,
                s.scalar(
                    sa.select(m.CanonicalLeadRow.id).where(
                        m.CanonicalLeadRow.lead_identity_id == old
                    )
                ),
            )
            is not None
        )
        assert stale_projections(s, current_version=2) == tuple(sorted(new, key=str))


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_a_join_leaves_one_lead_and_retires_the_absorbed_one(backend: Backend) -> None:
    bar = IdentityExclusions.from_values(emails=["ada@example.com"])
    remerge(
        backend.engine,
        [ada("alpha"), ada("beta", person__title="Countess")],
        exclusions=bar,
    )
    before = active_leads(backend.engine)
    assert len(before) == 2

    joined = remerge(backend.engine, at=NOW + timedelta(hours=1))

    after = active_leads(backend.engine)
    assert (joined.leads_created, joined.leads_retired) == (0, 1)
    assert len(after) == 1
    assert after < before
    (survivor,) = after
    (absorbed,) = before - after
    assert retired(backend.engine) == {absorbed: {survivor}}
    assert set(mapping(backend.engine).values()) == {survivor}


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_a_merge_that_covers_only_part_of_a_stored_lead_is_refused(
    backend: Backend,
) -> None:
    remerge(backend.engine, [ada("alpha"), ada("beta")])
    with Session(backend.engine) as session, session.begin():
        run_id = start_run(session)
        stored = load_lead_contributions(session)
        part = [c for c in stored.values() if c.source_name == "alpha"]
        merged = merge_contributions(part, exclusions=NONE, trust_ranks=RANKS)
        with pytest.raises(ValueError, match="every contribution of a lead"):
            persist_merge(
                session,
                run_id=run_id,
                batches=(),
                merged=merged,
                computed_at=NOW,
                projection_version=1,
                retention=RetentionPolicy(),
            )


# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_the_canonical_lead_keeps_its_primary_domain_decision_and_fingerprint(
    backend: Backend,
) -> None:
    remerge(
        backend.engine,
        [
            ada("alpha", company__domain="a.com", company__name="Engines"),
            ada("beta", company__domain=["a.com", "b.com"]),
        ],
    )
    with Session(backend.engine) as s:
        row = s.scalars(sa.select(m.CanonicalLeadRow)).one()
    assert (row.primary_domain, row.primary_domain_source) == (
        "a.com",
        TieSource.NOT_TIED.value,
    )
    assert row.projection_fingerprint == "f" * 64


# ------------------------------------------- invariant over random small scenarios

PEOPLE = ("ann", "bob", "cyd")


def _random_contribution(rng: random.Random, n: int) -> LeadContribution:
    person = rng.choice(PEOPLE)
    values: dict[str, Any] = {"person__full_name": person.title()}
    if rng.random() < 0.6:
        values["person__email"] = f"{rng.choice([person, 'info'])}@x.com"
        values["person__email_status"] = V
    if rng.random() < 0.5:
        values["person__linkedin_url"] = f"https://www.linkedin.com/in/{person}"
    values["person__title"] = f"t{n}"  # each contribution is its own observation
    return contribution(rng.choice(sorted(RANKS)), **values)


def _assert_one_person_one_active_lead(
    engine: Engine, exclusions: IdentityExclusions
) -> None:
    with Session(engine) as s:
        contributions = set(s.scalars(sa.select(m.SourceContribution.id)))
        links = dict(
            list(
                s.execute(
                    sa.select(
                        m.ContributionLead.contribution_id,
                        m.ContributionLead.lead_identity_id,
                    )
                )
            )
        )
        active = set(
            s.scalars(
                sa.select(m.LeadIdentity.id).where(m.LeadIdentity.retired_at.is_(None))
            )
        )
        gone = set(
            s.scalars(
                sa.select(m.LeadIdentity.id).where(
                    m.LeadIdentity.retired_at.is_not(None)
                )
            )
        )
        stored = load_lead_contributions(s)
        clusters = merge_contributions(
            list(stored.values()), exclusions=exclusions, trust_ranks=RANKS
        )
    # Every contribution belongs to exactly one lead, and that lead is active.
    assert set(links) == contributions
    assert set(links.values()) <= active
    assert not set(links.values()) & gone
    # Active leads are exactly the clusters of the log: no person in two of them.
    by_lead: dict[uuid.UUID, set[str]] = {}
    sha_of = {cid: contribution_sha(c) for cid, c in stored.items()}
    for cid, lead in links.items():
        by_lead.setdefault(lead, set()).add(sha_of[cid])
    expected = {
        frozenset(contribution_sha(c) for c in cl.contributions) for cl, _ in clusters
    }
    assert {frozenset(v) for v in by_lead.values()} == expected
    assert len(by_lead) == len(active)


# Verifies: specs/lead-source-adapters/requirements.md#8.12 (property)
@pytest.mark.parametrize("seed", range(6))
def test_one_person_is_never_in_two_active_leads_over_random_scenarios(
    backend: Backend, seed: int
) -> None:
    rng = random.Random(seed)
    n = 0
    for step in range(4):
        new: list[LeadContribution] = []
        for _ in range(rng.randint(0, 3)):
            n += 1
            new.append(_random_contribution(rng, n))
        bar = IdentityExclusions.from_values(
            emails=[e for e in ("info@x.com", "ann@x.com") if rng.random() < 0.4]
        )
        remerge(backend.engine, new, exclusions=bar, at=NOW + timedelta(hours=step))
        _assert_one_person_one_active_lead(backend.engine, bar)


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_a_row_written_before_0006_is_matched_and_never_stored_again(
    backend: Backend,
) -> None:
    remerge(backend.engine, [ada("alpha")])
    (lead,) = active_leads(backend.engine)
    # Make the row look pre-0006 (no identity, no origin) through Core, which the
    # ORM append-only guard does not see: only a migration-era row looks like this.
    with backend.engine.begin() as conn:
        conn.execute(
            sa.update(m.Base.metadata.tables["source_contribution"]).values(
                content_sha=None
            )
        )
        conn.execute(
            sa.update(m.Base.metadata.tables["contribution_field"]).values(
                confidence_origin=None
            )
        )
    rows = contribution_rows(backend.engine)

    again = remerge(backend.engine, [ada("alpha", at=NOW + timedelta(days=1))])

    assert (again.contributions, again.leads_created, again.leads_retired) == (0, 0, 0)
    assert active_leads(backend.engine) == {lead}
    assert contribution_rows(backend.engine) == rows
