"""Re-projection is incremental, and equal to a full recompute (follow-up 2026-10-06).

Requirements 8.12, 8.13 and 21.5. A run re-projects only the clusters its new
contributions touched (a cluster that no longer equals a stored Lead, or a Lead
projected under another version); a changed projection basis (Identity Exclusions,
Source Trust Ranks, rules revision), changed compliance reports or a newly stored
tie answer recompute every cluster and the run says why. Only Leads that changed
(created, updated, retired) are logged. A property test checks, on random scenarios,
that the incremental result is exactly what a full recompute writes.
"""

# ruff: noqa: F811 - fixtures imported from test_persistence_both_engines

import random
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from leadforge.lead_ingestion.base_source import LeadContribution
from leadforge.lead_ingestion.ingest_runner import run_ingestion
from leadforge.lead_ingestion.match_key_digest import MATCH_KEY_SECRET_ENV
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.remerge import merge_contributions, reproject
from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.store.contributions import contribution_sha
from leadforge.lead_ingestion.store.merged_leads import persist_merge
from leadforge.lead_ingestion.store.raw_responses import RetentionPolicy
from leadforge.lead_ingestion.tests.store_run_support import (
    Script,
    person,
    registry_of,
    scripted_source,
)
from leadforge.lead_ingestion.tests.test_lead_remerge import (
    NONE,
    NOW,
    RANKS,
    batches_of,
    contribution,
    start_run,
)
from leadforge.lead_ingestion.tests.test_persistence_both_engines import (  # noqa: F401 - fixtures
    Backend,
    backend,
    blank,
    postgres_url,
)
from leadforge.lead_ingestion.tests.test_remerge_run import _Resolver
from leadforge.lead_ingestion.tests.test_run_lifecycle_both_engines import (  # noqa: F401 - fixtures
    composed,
)

ADA = person("ada@example.com", "Ada Lovelace")
GRACE = person("grace@example.com", "Grace Hopper")
GRACE_TITLE = person("grace@example.com", "Grace Hopper", person__title="Admiral")


def _merge_lines(logs: Sequence[Mapping[str, Any]]) -> int:
    return sum(1 for e in logs if e.get("event") == "lead_merge")


def _run_row(engine: Engine, run_id: uuid.UUID) -> m.IngestionRun:
    with Session(engine) as s:
        run = s.get(m.IngestionRun, run_id)
        assert run is not None
        return run


# Verifies: specs/lead-source-adapters/requirements.md#8.12
# Verifies: specs/lead-source-adapters/requirements.md#21.5
async def test_an_identical_second_run_reprojects_and_logs_nothing(
    composed: Backend,
) -> None:
    def sources() -> Any:
        return registry_of(
            scripted_source("alpha", Script([ADA, GRACE])),
            scripted_source("beta", Script([ADA])),
        )

    with capture_logs() as first_logs:
        first = await run_ingestion(registry=sources())
    with capture_logs() as second_logs:
        second = await run_ingestion(registry=sources())

    assert _merge_lines(first_logs) == 1  # ada: two sources merged
    assert _run_row(composed.engine, first.run_id).clusters_reprojected == 2
    assert _merge_lines(second_logs) == 0
    row = _run_row(composed.engine, second.run_id)
    assert (row.clusters_reprojected, row.reprojection) == (0, "incremental")
    assert (second.stored.canonical_leads, second.stored.leads_created) == (0, 0)
    assert "re-projection: incremental, 0 clusters" in second.report_text.splitlines()


# Verifies: specs/lead-source-adapters/requirements.md#8.12
async def test_a_run_adding_one_contribution_reprojects_only_its_cluster(
    composed: Backend,
) -> None:
    # The same sources both times: a changed set of trust ranks is a new basis.
    await run_ingestion(
        registry=registry_of(
            scripted_source("alpha", Script([ADA, GRACE])),
            scripted_source("gamma", Script([])),
        )
    )
    with Session(composed.engine) as s:
        before = {
            r.lead_identity_id: (r.email, r.computed_at)
            for r in s.scalars(sa.select(m.CanonicalLeadRow))
        }

    with capture_logs() as logs:
        added = await run_ingestion(
            registry=registry_of(
                scripted_source("alpha", Script([])),
                scripted_source("gamma", Script([GRACE_TITLE])),
            )
        )

    row = _run_row(composed.engine, added.run_id)
    assert (row.clusters_reprojected, row.reprojection) == (1, "incremental")
    assert _merge_lines(logs) == 1
    with Session(composed.engine) as s:
        after = {
            r.lead_identity_id: (r.email, r.computed_at)
            for r in s.scalars(sa.select(m.CanonicalLeadRow))
        }
    assert after.keys() == before.keys()  # grace kept her lead
    changed = {lead for lead in after if after[lead] != before[lead]}
    assert {after[lead][0] for lead in changed} == {"grace@example.com"}


# Verifies: specs/lead-source-adapters/requirements.md#8.13
async def test_a_changed_basis_recomputes_all_flagged_and_logs_no_unchanged_lead(
    composed: Backend, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def sources() -> Any:  # ada is a merge of two sources: it would log a line
        return registry_of(
            scripted_source("alpha", Script([ADA, GRACE])),
            scripted_source("beta", Script([ADA])),
        )

    await run_ingestion(registry=sources())
    monkeypatch.setenv(MATCH_KEY_SECRET_ENV, "incremental-stable-secret-" + "s" * 32)
    exclusions = tmp_path / "exclusions.yaml"
    exclusions.write_text("emails:\n  - nobody@example.org\n", encoding="utf-8")
    with capture_logs() as logs:
        bumped = await run_ingestion(registry=sources(), exclusions_path=exclusions)

    row = _run_row(composed.engine, bumped.run_id)
    assert (row.clusters_reprojected, row.reprojection) == (
        2,
        "full: projection basis changed",
    )
    assert _merge_lines(logs) == 0  # every lead re-projected, none changed
    assert (
        "re-projection: full (projection basis changed), 2 clusters"
        in bumped.report_text.splitlines()
    )


# Verifies: specs/lead-source-adapters/requirements.md#8.13
def test_a_lead_projected_under_another_version_is_reprojected(
    backend: Backend,
) -> None:
    ada = contribution(
        "alpha", person__email="ada@example.com", person__email_status="verified"
    )
    grace = contribution(
        "alpha", person__email="grace@example.com", person__email_status="verified"
    )
    _persist(backend.engine, [ada, grace])  # both written under version 1
    with Session(backend.engine) as s, s.begin():
        s.execute(
            sa.update(m.CanonicalLeadRow)
            .where(m.CanonicalLeadRow.email == "grace@example.com")
            .values(projection_version=2)
        )
    with Session(backend.engine) as s, s.begin():
        plan = reproject(
            s, [], exclusions=NONE, trust_ranks=RANKS, now=NOW, projection_version=2
        )
    assert plan.full_reason is None
    [(_, result)] = plan.merged  # only ada's lead is stale
    assert result.lead is not None
    assert str(result.lead.email) == "ada@example.com"


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_a_new_compliance_report_recomputes_every_cluster(backend: Backend) -> None:
    ada = contribution(
        "alpha", person__email="ada@example.com", person__email_status="verified"
    )
    grace = contribution(
        "alpha", person__email="grace@example.com", person__email_status="verified"
    )
    _persist(backend.engine, [ada, grace])
    opt_out = contribution("beta", person__email="ada@example.com", opt_out=True)
    with Session(backend.engine) as s, s.begin():
        plan = reproject(
            s,
            [opt_out],
            exclusions=NONE,
            trust_ranks=RANKS,
            now=NOW,
            projection_version=1,
        )
    assert plan.full_reason == "compliance reports changed"
    assert len(plan.merged) == plan.clusters == 3  # the report is its own cluster


# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_a_tie_answer_stored_during_the_pass_recomputes_every_cluster(
    backend: Backend,
) -> None:
    """Grace's stored Lead holds the same primary-domain tie, unresolved; Ada's new
    live record gets it answered. Grace's members did not change, yet her Lead must
    now read the answer, so the pass becomes a full recompute."""

    def tied(email: str, mode: DataMode) -> LeadContribution:
        return contribution(
            "alpha",
            mode=mode,
            person__email=email,
            person__email_status="verified",
            company__domain=["a.com", "b.com"],
        )

    _persist(backend.engine, [tied("grace@example.com", DataMode.SYNTHETIC)])
    resolver = _Resolver()
    with Session(backend.engine) as s, s.begin():
        plan = reproject(
            s,
            [tied("ada@example.com", DataMode.LIVE)],
            exclusions=NONE,
            trust_ranks=RANKS,
            now=NOW,
            tie_resolver=lambda: resolver,
            projection_version=1,
        )
    assert resolver.calls == [("a.com", "b.com")]
    assert (plan.full_reason, len(plan.merged)) == ("tie answer stored", 2)
    assert {r.primary_domain for _, r in plan.merged} == {"b.com"}


# ---------------------------------------------- property: incremental == full recompute


_PEOPLE = (
    ("ada@example.com", "https://www.linkedin.com/in/ada", "Ada Lovelace"),
    ("grace@example.com", "https://www.linkedin.com/in/grace", "Grace Hopper"),
    ("alan@example.com", None, "Alan Turing"),
    ("edsger@example.com", None, "Edsger Dijkstra"),
)


def _random_contribution(rng: random.Random, index: int) -> LeadContribution:
    email, linkedin, name = rng.choice(_PEOPLE)
    source = rng.choice(sorted(RANKS))
    values: dict[str, Any] = {"person.full_name": name}
    shape = rng.randrange(5)
    if shape in (0, 1):
        values["person.email"] = email
        values["person.email_status"] = rng.choice(["verified", "unverified"])
    if shape in (1, 2) and linkedin:
        values["person.linkedin_url"] = linkedin
    if shape == 3:
        values["person.title"] = rng.choice(["CTO", "CEO", "Engineer"])
        values["company.domain"] = "example.com"
    if shape == 4:  # a keyless record: alone, or joined by its provider's record id
        values = {"person.full_name": f"Nobody {index}"}
        if rng.random() < 0.6:  # one record id of one source: re-fetches join
            source = sorted(RANKS)[0]
            values["person.provider_id"] = "rec-1"
    if rng.random() < 0.1:
        values["opt_out"] = True
        values.setdefault("person.email", email)
    return contribution(
        source,
        at=NOW + timedelta(minutes=index),
        **{k.replace(".", "__"): v for k, v in values.items()},
    )


def _persist(
    engine: Engine, new: list[LeadContribution], *, full: str | None = None
) -> Any:
    with Session(engine) as s, s.begin():
        run_id = start_run(s)
        plan = reproject(
            s,
            new,
            exclusions=NONE,
            trust_ranks=RANKS,
            now=NOW,
            projection_version=1,
            full_reason=full,
        )
        stored = persist_merge(
            s,
            run_id=run_id,
            batches=batches_of(*new),
            merged=plan.merged,
            computed_at=NOW,
            projection_version=1,
            projection_fingerprint="f" * 64,
            retention=RetentionPolicy(),
        )
        return plan, stored


def _leads(engine: Engine) -> set[tuple[Any, ...]]:
    """Each active lead's content keyed by the set of contributions it holds."""
    with Session(engine) as s:
        members: dict[uuid.UUID, set[str]] = {}
        for contribution_id, lead in s.execute(
            sa.select(
                m.ContributionLead.contribution_id, m.ContributionLead.lead_identity_id
            )
        ):
            sha = s.get(m.SourceContribution, contribution_id)
            assert sha is not None
            members.setdefault(lead, set()).add(str(sha.content_sha))
        out = set()
        for row in s.scalars(
            sa.select(m.CanonicalLeadRow)
            .join(
                m.LeadIdentity, m.LeadIdentity.id == m.CanonicalLeadRow.lead_identity_id
            )
            .where(m.LeadIdentity.retired_at.is_(None))
        ):
            employments = repr(row.employments).replace(
                row.lead_identity_id.hex, "<lead>"
            )
            out.add(
                (
                    frozenset(members[row.lead_identity_id]),
                    row.email,
                    row.linkedin_url,
                    row.full_name,
                    employments,
                    row.opt_out,
                    row.suppressed,
                    tuple(row.contributing_sources),
                    row.primary_domain,
                )
            )
        return out


# Verifies: specs/lead-source-adapters/requirements.md#8.12 (property)
@pytest.mark.parametrize("seed", range(24))  # 3 seeds join by record id
def test_incremental_runs_write_exactly_what_a_full_recompute_writes(
    backend: Backend, seed: int
) -> None:
    rng = random.Random(seed)
    pool = [_random_contribution(rng, i) for i in range(rng.randrange(4, 12))]
    for start in range(0, len(pool), 3):
        _persist(backend.engine, pool[start : start + 3])
    incremental = _leads(backend.engine)

    # A forced full recompute of the same store changes nothing at all...
    plan, stored = _persist(backend.engine, [], full="test")
    assert plan.full_reason == "test"
    assert (stored.changed, stored.leads_created, stored.leads_retired) == ((), 0, 0)
    assert _leads(backend.engine) == incremental

    # ...it equals the pure merge of the whole log from scratch (the oracle)...
    oracle = Counter(
        (
            str(r.lead.email) if r.lead.email else None,
            str(r.lead.linkedin_url) if r.lead.linkedin_url else None,
            r.lead.full_name,
            r.lead.opt_out,
            r.lead.suppressed,
            tuple(r.contributing_sources),
            r.primary_domain,
        )
        for _, r in merge_contributions(
            {contribution_sha(c): c for c in pool}.values(),
            exclusions=NONE,
            trust_ranks=RANKS,
        )
        if r.lead is not None
    )
    assert (
        Counter(
            (e, li, n, o, sup, src, d) for _, e, li, n, _, o, sup, src, d in incremental
        )
        == oracle
    )

    # ...and repeating the last batch re-projects nothing.
    again, _ = _persist(backend.engine, pool[-1:])
    assert again.merged == ()
