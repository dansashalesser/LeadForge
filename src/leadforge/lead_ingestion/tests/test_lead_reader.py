"""Loading a stored lead back (follow-up, user request 2026-10-06), on both engines.

``store.lead_reader`` is the read path: ``load_lead`` returns the ``CanonicalLead`` the
projection produced (the same type, equal to it) with what the store keeps about it:
field provenance (source, Field Confidence, Confidence Origin, superseded losers),
agreement, primary domain and its tie flag, projection version and stale flag,
retirement with successor pointers, and attached web evidence of its company.
``list_leads`` pages them deterministically; ``find_lead`` looks one up by Match Key
through the ``match_keys`` normalizers. Every read is a bounded number of queries.
"""

# ruff: noqa: F811 - fixtures imported from test_persistence_both_engines

import dataclasses
import json
import random
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine
from sqlalchemy.orm import Session
from structlog.testing import capture_logs
from typer.testing import CliRunner

from leadforge.lead_ingestion import cli
from leadforge.lead_ingestion.base_source import LeadContribution
from leadforge.lead_ingestion.clustering import IdentityCluster
from leadforge.lead_ingestion.companies import lead_company_id
from leadforge.lead_ingestion.match_key_digest import DIGEST_HEX_CHARS
from leadforge.lead_ingestion.match_keys import IdentityExclusions
from leadforge.lead_ingestion.models import (
    CanonicalLead,
    CompanySignal,
    ConfidenceOrigin,
    DataMode,
    Employment,
    FieldProvenance,
    IntentSignal,
    ProviderCompanyId,
    TechSignal,
    UntrustedText,
)
from leadforge.lead_ingestion.projection import ProjectionResult, project_lead
from leadforge.lead_ingestion.remerge import project_with_store
from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.store.contributions import contribution_sha
from leadforge.lead_ingestion.store.lead_reader import (
    StoredLead,
    SuccessionError,
    find_lead,
    list_leads,
    load_lead,
)
from leadforge.lead_ingestion.store.merged_leads import (
    current_leads,
    lead_owned_employments,
    persist_merge,
)
from leadforge.lead_ingestion.store.raw_responses import RetentionPolicy
from leadforge.lead_ingestion.store.store_key import store_digest
from leadforge.lead_ingestion.tests.test_end_to_end_zero_credential import (  # noqa: F401 - fixtures
    restore_structlog,
)
from leadforge.lead_ingestion.tests.test_lead_remerge import (
    NOW,
    RANKS,
    V,
    ada,
    batches_of,
    contribution,
    start_run,
)
from leadforge.lead_ingestion.tests.test_persistence_both_engines import (  # noqa: F401 - fixtures
    Backend,
    _alembic,
    backend,
    blank,
    postgres_url,
)
from leadforge.lead_ingestion.tests.test_run_lifecycle_both_engines import (  # noqa: F401 - fixtures
    composed,
)
from leadforge.lead_ingestion.tie_resolution import TieSource

# ----------------------------------------------------------------------- helpers


@contextmanager
def counted(engine: Engine) -> Iterator[list[str]]:
    """Every SQL statement the engine executes inside the block."""
    statements: list[str] = []

    def record(*args: Any) -> None:
        statements.append(str(args[2]))

    sa.event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        sa.event.remove(engine, "before_cursor_execute", record)


def stated(source: str, path: str, value: Any, confidence: float) -> LeadContribution:
    """Ada, by one source, with ``path`` at a provider-stated confidence."""
    plain = ada(source)
    record = FieldProvenance(
        canonical_path=path,
        source_name=source,
        data_mode=DataMode.SYNTHETIC,
        fetched_at=NOW,
        raw_field_path=f"raw.{path}",
        confidence_origin=ConfidenceOrigin.PROVIDER_STATED,
        untrusted=False,
        confidence=confidence,
        confidence_raw=str(int(confidence * 100)),
        confidence_scale="0-100",
    )
    return plain.model_copy(
        update={
            "values": {**plain.values, path: value},
            "provenance": (*plain.provenance, record),
        }
    )


def lead_ids(
    session: Session, merged: Sequence[tuple[IdentityCluster, ProjectionResult]]
) -> list[uuid.UUID]:
    """The stored lead of each merged cluster, through the derived mapping."""
    stored = dict(
        session.execute(
            sa.select(m.SourceContribution.content_sha, m.SourceContribution.id)
        ).all()
    )
    links = current_leads(session)
    out = []
    for cluster, _ in merged:
        sha = store_digest(session, contribution_sha(cluster.contributions[0]))
        out.append(links[stored[sha]])
    return out


def as_stored(lead: CanonicalLead, lead_id: uuid.UUID) -> CanonicalLead:
    """The projected lead as the store keeps it, through the store's one function
    (``merged_leads.lead_owned_employments``): a domainless company carries the
    lead's persisted company id, never the projection's in-memory pseudonym."""
    return lead.model_copy(
        update={"employments": lead_owned_employments(lead.employments, lead_id)}
    )


def assert_round_trip(
    loaded: StoredLead | None, result: ProjectionResult, lead_id: uuid.UUID
) -> None:
    assert loaded is not None
    assert result.lead is not None
    assert loaded.lead_id == lead_id
    assert loaded.lead == as_stored(result.lead, lead_id)
    # Every record, agreeing sources included, in the projection's order.
    assert loaded.provenance == result.provenance
    assert loaded.agreement == result.agreement
    assert loaded.contributing_sources == result.contributing_sources
    assert loaded.primary_domain == result.primary_domain
    assert loaded.primary_domain_source == result.primary_domain_source
    assert loaded.primary_domain_flagged == result.primary_domain_flagged


def merge(
    engine: Engine,
    contributions: Sequence[LeadContribution],
    *,
    exclusions: IdentityExclusions | None = None,
    hours: int = 0,
) -> list[tuple[uuid.UUID, ProjectionResult]]:
    """``remerge`` that also returns each merged lead's id and projection."""
    at = NOW + timedelta(hours=hours)
    with Session(engine) as session, session.begin():
        run_id = start_run(session)
        merged = project_with_store(
            session,
            contributions,
            exclusions=exclusions or IdentityExclusions(),
            trust_ranks=RANKS,
            now=at,
        )
        persist_merge(
            session,
            run_id=run_id,
            batches=batches_of(*contributions),
            merged=merged,
            computed_at=at,
            projection_version=1,
            projection_fingerprint="f" * 64,
            retention=RetentionPolicy(),
        )
        ids = lead_ids(session, merged)
    return [(i, r) for i, (_, r) in zip(ids, merged, strict=True) if r.lead]


def only(engine: Engine, contributions: Sequence[LeadContribution]) -> uuid.UUID:
    (pair,) = merge(engine, contributions)
    return pair[0]


# ---------------------------------------------------------- load_lead: hydration


# Verifies: specs/lead-source-adapters/requirements.md#8.7
# Verifies: specs/lead-source-adapters/requirements.md#9.5
def test_load_lead_returns_the_projected_lead_with_its_field_provenance(
    backend: Backend,
) -> None:
    contributions = [
        stated("alpha", "person.title", "CTO", 0.9),
        stated("beta", "person.title", "Countess", 0.4),
        ada("alpha", company__domain="acme.com", company__name="Acme"),
        ada("gamma", company__domain="acme.com"),
    ]
    ((lead_id, result),) = merge(backend.engine, contributions)

    with Session(backend.engine) as s:
        loaded = load_lead(s, lead_id)

    assert_round_trip(loaded, result, lead_id)
    assert loaded is not None
    assert isinstance(loaded.lead, CanonicalLead)
    titles = loaded.provenance_of("person.title")
    assert [(p.source_name, p.superseded) for p in titles] == [
        ("alpha", False),
        ("beta", True),
    ]
    stated_conf = {
        p.source_name: (p.confidence, p.confidence_origin)
        for p in titles
        if p.confidence_origin is ConfidenceOrigin.PROVIDER_STATED
    }
    assert stated_conf == {
        "alpha": (0.9, ConfidenceOrigin.PROVIDER_STATED),
        "beta": (0.4, ConfidenceOrigin.PROVIDER_STATED),
    }
    assert loaded.sources_of("person.title") == ("alpha", "beta")
    # gamma agrees with alpha's domain: it is provenance, not only a count.
    assert loaded.sources_of("company.domain") == ("alpha", "gamma")
    assert dict(loaded.agreement)["company.domain"] == 2
    assert loaded.primary_domain == "acme.com"
    assert loaded.primary_domain_source is TieSource.NOT_TIED
    assert loaded.projection_version == 1
    assert loaded.projection_fingerprint == "f" * 64
    assert loaded.computed_at == NOW
    assert (loaded.retired, loaded.retired_at, loaded.successor_ids) == (
        False,
        None,
        (),
    )


# Verifies: specs/lead-source-adapters/requirements.md#9.5
def test_an_unknown_id_or_a_lead_with_no_person_loads_as_none(
    backend: Backend,
) -> None:
    merge(backend.engine, [contribution("alpha", company__domain="acme.com")])
    with Session(backend.engine) as s:
        assert load_lead(s, uuid.uuid4()) is None
        (identity,) = s.scalars(sa.select(m.LeadIdentity.id)).all()
        assert load_lead(s, identity) is None  # a company-only cluster: no lead


# Verifies: specs/lead-source-adapters/requirements.md#8.4
# Verifies: specs/lead-source-adapters/requirements.md#11.4
def test_email_role_contacts_suppression_and_signals_round_trip(
    backend: Backend,
) -> None:
    """Fields no adapter fills today (signals, provider ids) round-trip too."""
    ada_c = contribution(
        "alpha",
        person__email="info@acme.com",
        person__email_status="accept_all",
        person__full_name="Ada Lovelace",
        company__name="Analytical Engines",
    )
    cluster = IdentityCluster("c", (ada_c,))
    result = project_lead(cluster, RANKS)
    assert result.lead is not None
    company = CompanySignal(
        company_id="co-0123456789abcdef",
        provider_ids=(ProviderCompanyId(source="alpha", id="42"),),
        name="Analytical Engines",
        domains=("acme.com",),
        tech_signals=(TechSignal(label="python", strength=0.5),),
    )
    lead = result.lead.model_copy(
        update={
            "email_is_role_address": True,
            "role_contact_emails": ("hello@acme.com", "sales@acme.com"),
            "opt_out": True,
            "suppressed": True,
            "employments": (
                Employment(company=company, title="CTO", is_current=True),
                Employment(
                    company=CompanySignal(company_id="co-x", name="Navy"),
                    is_current=False,
                ),
            ),
            "tech_signals": (TechSignal(label="rust", strength=1.0),),
            "intent_signals": (IntentSignal(label="crm", strength=0.25),),
        }
    )
    with Session(backend.engine) as s, s.begin():
        persist_merge(
            s,
            run_id=start_run(s),
            batches=batches_of(ada_c),
            merged=[(cluster, dataclasses.replace(result, lead=lead))],
            computed_at=NOW,
            projection_version=1,
            retention=RetentionPolicy(),
        )
    with Session(backend.engine) as s:
        (lead_id,) = s.scalars(sa.select(m.LeadIdentity.id)).all()
        loaded = load_lead(s, lead_id)

    assert loaded is not None
    assert loaded.lead == as_stored(lead, lead_id)
    assert loaded.lead.employments[1].company.company_id == lead_company_id(lead_id)
    assert loaded.lead.email_status.value == "accept_all"
    assert (loaded.lead.opt_out, loaded.lead.suppressed) == (True, True)


# Verifies: specs/lead-source-adapters/requirements.md#8.13
def test_the_stale_flag_compares_the_stored_version_with_the_current_one(
    backend: Backend,
) -> None:
    lead_id = only(backend.engine, [ada("alpha")])
    with Session(backend.engine) as s:
        assert load_lead(s, lead_id).stale is None  # type: ignore[union-attr]
        assert load_lead(s, lead_id, current_version=1).stale is False  # type: ignore[union-attr]
        assert load_lead(s, lead_id, current_version=2).stale is True  # type: ignore[union-attr]
        run = s.scalars(sa.select(m.IngestionRun)).first()
        assert run is not None
        run.status, run.projection_version = "completed", 3
        run.projection_fingerprint = "e" * 64
        s.flush()
        # Without an explicit version, the newest completed run's stamp is current.
        assert load_lead(s, lead_id).stale is True  # type: ignore[union-attr]


# ------------------------------------------------------------- retired leads


def _joined(engine: Engine) -> tuple[uuid.UUID, uuid.UUID]:
    """Two leads joined by a lifted exclusion: (absorbed, survivor)."""
    bar = IdentityExclusions.from_values(emails=["ada@example.com"])
    merge(engine, [ada("alpha"), ada("beta", person__title="Countess")], exclusions=bar)
    with Session(engine) as s:
        before = set(s.scalars(sa.select(m.CanonicalLeadRow.lead_identity_id)))
    merge(engine, [], hours=1)
    with Session(engine) as s:
        (survivor,) = s.scalars(
            sa.select(m.LeadIdentity.id).where(m.LeadIdentity.retired_at.is_(None))
        ).all()
    (absorbed,) = before - {survivor}
    return absorbed, survivor


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_a_retired_lead_loads_marked_retired_with_its_successor(
    backend: Backend,
) -> None:
    absorbed, survivor = _joined(backend.engine)
    with Session(backend.engine) as s:
        old = load_lead(s, absorbed)
        followed = load_lead(s, absorbed, follow_successor=True)
        same = load_lead(s, survivor, follow_successor=True)

    assert old is not None
    assert old.retired
    assert old.retired_at is not None
    assert old.successor_ids == (survivor,)
    assert old.stale is None
    assert followed is not None
    assert followed.lead_id == survivor
    assert not followed.retired
    assert same is not None
    assert same.lead_id == survivor


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_following_a_split_lead_names_every_active_successor(
    backend: Backend,
) -> None:
    merge(backend.engine, [ada("alpha"), ada("beta", person__title="Countess")])
    with Session(backend.engine) as s:
        (old,) = s.scalars(sa.select(m.LeadIdentity.id)).all()
    bar = IdentityExclusions.from_values(emails=["ada@example.com"])
    merge(backend.engine, [], exclusions=bar, hours=1)
    with Session(backend.engine) as s:
        loaded = load_lead(s, old)
        assert loaded is not None
        assert len(loaded.successor_ids) == 2
        with pytest.raises(SuccessionError) as caught:
            load_lead(s, old, follow_successor=True)
    assert set(caught.value.lead_ids) == set(loaded.successor_ids)
    assert "ada" not in str(caught.value)


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_following_successors_is_cycle_safe(backend: Backend) -> None:
    a = only(backend.engine, [ada("alpha")])
    with Session(backend.engine) as s, s.begin():
        b = m.LeadIdentity(created_at=NOW, retired_at=NOW)
        s.add(b)
        s.flush()
        s.get(m.LeadIdentity, a).retired_at = NOW  # type: ignore[union-attr]
        s.add(m.LeadSuccession(predecessor_id=a, successor_id=b.id, recorded_at=NOW))
        s.add(m.LeadSuccession(predecessor_id=b.id, successor_id=a, recorded_at=NOW))
    with Session(backend.engine) as s, pytest.raises(SuccessionError) as caught:
        load_lead(s, a, follow_successor=True)
    assert caught.value.lead_ids == ()


# --------------------------------------------------------------- list_leads


def _people(engine: Engine, n: int, start: int = 0) -> list[uuid.UUID]:
    contributions = [
        contribution(
            "alpha",
            person__email=f"p{i}@acme.com",
            person__email_status=V,
            person__full_name=f"Person {i}",
            company__domain="acme.com" if i % 2 else "navy.mil",
        )
        for i in range(start, start + n)
    ]
    return [i for i, _ in merge(engine, contributions)]


# Verifies: specs/lead-source-adapters/requirements.md#9.5
def test_list_leads_pages_in_a_deterministic_order_with_a_keyset_cursor(
    backend: Backend,
) -> None:
    ids = sorted(_people(backend.engine, 7), key=str)
    with Session(backend.engine) as s:
        everything = [x.lead_id for x in list_leads(s, limit=100)]
        pages: list[uuid.UUID] = []
        after = None
        while True:
            page = list_leads(s, limit=3, after=after)
            pages.extend(x.lead_id for x in page)
            if len(page) < 3:
                break
            after = page[-1].lead_id
    assert everything == ids
    assert pages == ids  # no lead twice, none skipped


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_list_leads_hides_retired_leads_unless_asked(backend: Backend) -> None:
    absorbed, survivor = _joined(backend.engine)
    with Session(backend.engine) as s:
        assert [x.lead_id for x in list_leads(s)] == [survivor]
        both = list_leads(s, include_retired=True)
    assert {x.lead_id for x in both} == {absorbed, survivor}
    assert [x.lead_id for x in both] == sorted({absorbed, survivor}, key=str)


# Verifies: specs/lead-source-adapters/requirements.md#9.5
def test_list_leads_filters_by_company_id(backend: Backend) -> None:
    _people(backend.engine, 6)
    with Session(backend.engine) as s:
        acme = next(
            e.company.company_id
            for x in list_leads(s)
            for e in x.lead.employments
            if e.company.domains == ("acme.com",)
        )
        at_acme = list_leads(s, company_id=acme, limit=2)
        rest = list_leads(s, company_id=acme, after=at_acme[-1].lead_id)
        assert list_leads(s, company_id="co-none") == ()
    found = [*at_acme, *rest]
    assert len(found) == 3
    assert all(x.lead.employments[0].company.company_id == acme for x in found)


# Verifies: specs/lead-source-adapters/requirements.md#9.5
def test_list_leads_rejects_a_limit_below_one(backend: Backend) -> None:
    with Session(backend.engine) as s, pytest.raises(ValueError, match="limit"):
        list_leads(s, limit=0)


# ---------------------------------------------------------------- find_lead


# Verifies: specs/lead-source-adapters/requirements.md#8.1
def test_find_lead_matches_by_the_normalized_match_key(backend: Backend) -> None:
    lead_id = only(
        backend.engine,
        [ada("alpha", person__linkedin_url="https://www.linkedin.com/in/ada")],
    )
    with Session(backend.engine) as s:
        by_email = find_lead(s, email="  ADA@Example.com ")
        by_url = find_lead(s, linkedin_url="http://WWW.LinkedIn.com/in/Ada/?x=1#y")
        both = find_lead(
            s, email="ada@example.com", linkedin_url="https://www.linkedin.com/in/ada"
        )
        wrong_pair = find_lead(
            s, email="ada@example.com", linkedin_url="https://linkedin.com/in/bob"
        )
        assert find_lead(s, email="bob@example.com") == ()
        with pytest.raises(ValueError, match="email or linkedin_url"):
            find_lead(s)
    assert [x.lead_id for x in by_email] == [lead_id]
    assert [x.lead_id for x in by_url] == [lead_id]
    assert [x.lead_id for x in both] == [lead_id]
    assert wrong_pair == ()


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_find_lead_never_returns_a_retired_lead(backend: Backend) -> None:
    _, survivor = _joined(backend.engine)
    with Session(backend.engine) as s:
        found = find_lead(s, email="ada@example.com")
    assert [x.lead_id for x in found] == [survivor]


def _index(engine: Engine) -> list[tuple[uuid.UUID, str, str]]:
    with Session(engine) as s:
        return [
            (lead, kind, digest)
            for lead, kind, digest in s.execute(
                sa.select(
                    m.LeadMatchKey.lead_identity_id,
                    m.LeadMatchKey.kind,
                    m.LeadMatchKey.digest,
                )
            )
        ]


# Verifies: specs/lead-source-adapters/requirements.md#8.1
# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_the_match_key_index_holds_keyed_digests_of_active_leads_only(
    backend: Backend,
) -> None:
    absorbed, survivor = _joined(backend.engine)
    rows = _index(backend.engine)
    assert [(lead, kind) for lead, kind, _ in rows] == [(survivor, "verified_email")]
    assert all(
        len(d) == DIGEST_HEX_CHARS and set(d) <= set("0123456789abcdef")
        for _, _, d in rows
    )
    assert "@" not in repr(rows)  # no plain value
    assert "example" not in repr(rows)
    assert absorbed not in {lead for lead, _, _ in rows}


# Verifies: specs/lead-source-adapters/requirements.md#8.1
def test_find_lead_looks_up_the_index_and_normalizes_like_clustering(
    backend: Backend,
) -> None:
    lead_id = only(
        backend.engine,
        [
            contribution(
                "alpha",
                person__email="Ada.L@Example.COM",
                person__email_status=V,
                person__full_name="Ada Lovelace",
                person__linkedin_url="https://uk.linkedin.com/in/Ada-L/",
            )
        ],
    )
    with Session(backend.engine) as s:
        for email, url in (
            ("ada.l@example.com", None),
            (" ADA.L@EXAMPLE.com ", None),
            (None, "www.linkedin.com/in/ada-l"),
            (None, "https://de.linkedin.com/in/ADA-L?trk=x"),
            ("ada.l@example.com", "linkedin.com/in/ada-l"),
        ):
            found = find_lead(s, email=email, linkedin_url=url)
            assert [x.lead_id for x in found] == [lead_id], (email, url)
        # A given criterion is never silently dropped.
        with pytest.raises(ValueError, match="unusable"):
            find_lead(s, email="no-at-sign", linkedin_url="linkedin.com/in/ada-l")
    with Session(backend.engine) as s, s.begin():
        s.execute(sa.delete(m.LeadMatchKey))
    with Session(backend.engine) as s:  # no scan behind the index
        assert find_lead(s, email="ada.l@example.com") == ()


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_a_re_merge_moves_the_index_with_the_lead(backend: Backend) -> None:
    """A re-merge rewrites the lead's index rows: a key it gained finds it."""
    lead_id = only(backend.engine, [ada("alpha")])
    merge(
        backend.engine,
        [ada("gamma", person__linkedin_url="https://linkedin.com/in/ada")],
        hours=1,
    )
    with Session(backend.engine) as s:
        by_url = find_lead(s, linkedin_url="https://linkedin.com/in/ada")
        by_email = find_lead(s, email="ada@example.com")
    assert [x.lead_id for x in by_url] == [lead_id]
    assert [x.lead_id for x in by_email] == [lead_id]
    assert sorted(kind for _, kind, _ in _index(backend.engine)) == [
        "linkedin_url",
        "verified_email",
    ]


# Verifies: specs/lead-source-adapters/requirements.md#9.5
def test_migration_0011_backfills_the_index_and_reverses(blank: Backend) -> None:
    _alembic(blank, "upgrade", "0010")
    active, retired = uuid.uuid4(), uuid.uuid4()
    with blank.engine.begin() as conn:
        for lead, gone in ((active, None), (retired, NOW)):
            conn.execute(
                sa.insert(m.LeadIdentity).values(
                    id=lead, created_at=NOW, retired_at=gone
                )
            )
            conn.execute(
                sa.insert(m.CanonicalLeadRow).values(
                    id=uuid.uuid4(),
                    lead_identity_id=lead,
                    email="Ada@Example.com",
                    email_status="verified",
                    linkedin_url="https://uk.linkedin.com/in/Ada",
                    full_name="Ada Lovelace",
                    opt_out=False,
                    suppressed=False,
                    email_is_role_address=False,
                    contributing_sources=["alpha"],
                    computed_at=NOW,
                    projection_version=1,
                )
            )

    _alembic(blank, "upgrade", "head")
    assert {lead for lead, _, _ in _index(blank.engine)} == {active}
    with Session(blank.engine) as s:
        assert [x.lead_id for x in find_lead(s, email="ada@example.com")] == [active]
        url = find_lead(s, linkedin_url="https://www.linkedin.com/in/ada")
        assert [x.lead_id for x in url] == [active]

    _alembic(blank, "downgrade", "0010")
    inspector = sa.inspect(blank.engine)
    assert "lead_match_key" not in inspector.get_table_names()
    provenance = inspector.get_columns("canonical_field_provenance")
    assert "agreeing_field_ids" not in {c["name"] for c in provenance}
    with blank.engine.connect() as conn:
        names = set(conn.scalars(sa.select(m.StoreSecret.name)))
    assert names == {"content_digest"}
    _alembic(blank, "upgrade", "head")
    assert {lead for lead, _, _ in _index(blank.engine)} == {active}


# ---------------------------------------------------------------- web evidence


def _evidence(attachment: str, url: str) -> LeadContribution:
    values: dict[str, Any] = {
        "company.domain": "acme.com",
        "company.web_evidence.attachment": attachment,
        "company.web_evidence.url": url,
        "company.web_evidence.snippet": UntrustedText(
            value="Acme uses Rust", truncated=False, original_length=14
        ),
        "company.web_evidence.signal_label": "rust",
        "company.web_evidence.signal_strength": 0.25,
    }
    if attachment == "unattached":
        del values["company.domain"]
    return LeadContribution(
        source_name="google_search",
        values=values,
        provenance=tuple(
            FieldProvenance(
                canonical_path=path,
                source_name="google_search",
                data_mode=DataMode.SYNTHETIC,
                fetched_at=NOW,
                raw_field_path=f"raw.{path}",
                confidence_origin=ConfidenceOrigin.NONE,
                untrusted=isinstance(value, UntrustedText),
            )
            for path, value in values.items()
        ),
    )


# Verifies: specs/lead-source-adapters/requirements.md#24.4
def test_a_lead_carries_the_web_evidence_attached_to_its_company(
    backend: Backend,
) -> None:
    rows = [
        ada("alpha", company__domain="acme.com"),
        _evidence("own_domain", "https://acme.com/blog"),
        _evidence("unattached", "https://elsewhere.net/x"),
    ]
    with Session(backend.engine) as s, s.begin():
        run_id = start_run(s, *RANKS, "google_search")
        merged = project_with_store(
            s,
            rows,
            exclusions=IdentityExclusions(),
            trust_ranks={**RANKS, "google_search": 4},
            now=NOW,
        )
        persist_merge(
            s,
            run_id=run_id,
            batches=batches_of(*rows),
            merged=merged,
            computed_at=NOW,
            projection_version=1,
            retention=RetentionPolicy(),
        )
        merged_ids = [
            i for i, (_, r) in zip(lead_ids(s, merged), merged, strict=True) if r.lead
        ]
    (lead_id,) = merged_ids
    with Session(backend.engine) as s:
        loaded = load_lead(s, lead_id)

    assert loaded is not None
    (evidence,) = loaded.web_evidence
    assert evidence.source_name == "google_search"
    assert evidence.attachment == "own_domain"
    assert evidence.values["url"] == "https://acme.com/blog"
    assert evidence.values["snippet"].value == "Acme uses Rust"
    assert isinstance(evidence.values["snippet"], UntrustedText)
    assert "Acme uses Rust" not in repr(evidence)


# ---------------------------------------------------------------- no N+1


# Verifies: specs/lead-source-adapters/requirements.md#9.5
def test_reads_take_a_bounded_number_of_queries(backend: Backend) -> None:
    ids = _people(backend.engine, 2)
    with Session(backend.engine) as s:
        load_lead(s, ids[0], current_version=1)  # warm the session (store key etc.)
    with Session(backend.engine) as s, counted(backend.engine) as one:
        assert load_lead(s, ids[0]) is not None
    assert len(one) <= 6, one

    with Session(backend.engine) as s, counted(backend.engine) as two:
        assert len(list_leads(s)) == 2
    with Session(backend.engine) as s, counted(backend.engine) as found_of_two:
        assert len(find_lead(s, email="p1@acme.com")) == 1
    _people(backend.engine, 10, start=2)
    with Session(backend.engine) as s, counted(backend.engine) as twelve:
        assert len(list_leads(s)) == 12
    assert len(twelve) == len(two) <= 6

    with Session(backend.engine) as s, counted(backend.engine) as found:
        assert len(find_lead(s, email="p1@acme.com")) == 1
    # The index key (once per session), the index, then load_lead's reads.
    assert len(found) == len(found_of_two) <= 8
    # The index lookup reads only the matching key: canonical_lead is read by id.
    scans = [q for q in found if "FROM canonical_lead" in q]
    assert scans
    assert all("lead_identity_id IN" in q for q in scans), scans


# ------------------------------------------------- round trip, random scenarios

PEOPLE = ("ann", "bob", "cyd")
STATUSES = ("verified", "unverified", "accept_all", "invalid")


def _random_contribution(rng: random.Random, n: int) -> LeadContribution:
    who = rng.choice(PEOPLE)
    values: dict[str, Any] = {"person__full_name": who.title()}
    if rng.random() < 0.6:
        values["person__email"] = f"{rng.choice([who, 'info'])}@x.com"
        values["person__email_status"] = rng.choice(STATUSES)
    if rng.random() < 0.4:
        values["person__linkedin_url"] = f"https://www.linkedin.com/in/{who}"
    if rng.random() < 0.5:
        values["company__domain"] = rng.choice(["x.com", ["x.com", "y.com"]])
    if rng.random() < 0.3:
        values["company__name"] = rng.choice(["Ex", "Why"])
    if rng.random() < 0.15:
        values[rng.choice(["opt_out", "suppressed"])] = True
    values["person__title"] = f"t{n}"  # each contribution is its own observation
    return contribution(rng.choice(sorted(RANKS)), **values)


# Verifies: specs/lead-source-adapters/requirements.md#8.12 (property)
# Verifies: specs/lead-source-adapters/requirements.md#9.5 (property)
@pytest.mark.parametrize("seed", range(6))
def test_every_saved_projection_loads_back_equal_over_random_scenarios(
    backend: Backend, seed: int
) -> None:
    rng = random.Random(seed)
    n = 0
    for step in range(4):
        new: list[LeadContribution] = []
        for _ in range(rng.randint(1, 4)):
            n += 1
            new.append(_random_contribution(rng, n))
        bar = IdentityExclusions.from_values(
            emails=[e for e in ("info@x.com", "ann@x.com") if rng.random() < 0.3]
        )
        saved = merge(backend.engine, new, exclusions=bar, hours=step)
        with Session(backend.engine) as s:
            for lead_id, result in saved:
                assert_round_trip(load_lead(s, lead_id), result, lead_id)
            listed = {x.lead_id for x in list_leads(s, limit=1000)}
            active = set(
                s.scalars(
                    sa.select(m.CanonicalLeadRow.lead_identity_id)
                    .join(m.LeadIdentity)
                    .where(m.LeadIdentity.retired_at.is_(None))
                )
            )
            indexed = set(s.scalars(sa.select(m.LeadMatchKey.lead_identity_id)))
            for x in list_leads(s, limit=1000):
                for email, url in ((x.lead.email, None), (None, x.lead.linkedin_url)):
                    if email is None and url is None:
                        continue
                    found = find_lead(
                        s,
                        email=None if email is None else str(email),
                        linkedin_url=None if url is None else str(url),
                    )
                    assert x.lead_id in {f.lead_id for f in found}
        assert indexed <= active  # never a retired lead
        assert listed == active  # every active lead listed, none twice
        assert {i for i, _ in saved} <= active


# ------------------------------------------------------------------- the CLI


def _cli_lead(engine: Engine) -> uuid.UUID:
    return only(
        engine,
        [
            ada(
                "alpha",
                person__linkedin_url="https://www.linkedin.com/in/ada-l",
                company__domain="acme.com",
                company__name="Acme",
            )
        ],
    )


# Verifies: specs/lead-source-adapters/requirements.md#22.1
def test_leads_show_masks_contact_identifiers_unless_revealed(
    composed: Backend, restore_structlog: None
) -> None:
    lead_id = _cli_lead(composed.engine)
    runner = CliRunner()

    with capture_logs() as logs:
        masked = runner.invoke(cli.app, ["leads", "show", str(lead_id)])
        revealed = runner.invoke(cli.app, ["leads", "show", str(lead_id), "--reveal"])

    assert masked.exit_code == 0, masked.output
    assert str(lead_id) in masked.stdout
    assert "a***@example.com" in masked.stdout
    assert "ada@example.com" not in masked.stdout
    assert "ada-l" not in masked.stdout
    assert "Ada Lovelace" in masked.stdout
    assert "acme.com" in masked.stdout
    assert revealed.exit_code == 0, revealed.output
    assert "ada@example.com" in revealed.stdout
    assert "linkedin.com/in/ada-l" in revealed.stdout
    for text in (masked.stderr, revealed.stderr, repr(logs)):
        assert "ada@example.com" not in text
        assert "ada-l" not in text


# Verifies: specs/lead-source-adapters/requirements.md#22.1
def test_leads_show_and_list_print_json_masked_unless_revealed(
    composed: Backend, restore_structlog: None
) -> None:
    lead_id = _cli_lead(composed.engine)
    runner = CliRunner()
    with capture_logs() as logs:
        masked = runner.invoke(cli.app, ["leads", "show", str(lead_id), "--json"])
        revealed = runner.invoke(
            cli.app, ["leads", "show", str(lead_id), "--json", "--reveal"]
        )
        listed = runner.invoke(cli.app, ["leads", "list", "--json"])

    assert masked.exit_code == 0, masked.output
    shown = json.loads(masked.stdout)
    assert shown["lead_id"] == str(lead_id)
    assert shown["lead"]["email"] == "a***@example.com"
    assert shown["lead"]["linkedin_url"] == "linkedin.com/***"
    assert shown["lead"]["full_name"] == "Ada Lovelace"
    assert {p["source_name"] for p in shown["provenance"]} == {"alpha"}
    assert "ada-l" not in masked.stdout
    assert json.loads(revealed.stdout)["lead"]["email"] == "ada@example.com"
    page = json.loads(listed.stdout)
    assert [x["lead_id"] for x in page["leads"]] == [str(lead_id)]
    assert page["leads"][0]["lead"]["email"] == "a***@example.com"
    assert page["next_after"] is None
    for text in (masked.stderr, revealed.stderr, repr(logs)):
        assert "ada@example.com" not in text


# Verifies: specs/lead-source-adapters/requirements.md#22.1
def test_leads_show_escapes_control_characters_in_stored_text(
    composed: Backend, restore_structlog: None
) -> None:
    """Provider text reaches a terminal: an escape sequence must not run there."""
    hostile = contribution(
        "alpha",
        person__email="ada@example.com",
        person__email_status=V,
        person__full_name="Ada\x1b[2J Lovelace",
        person__title="CTO\rOwned",
        company__name="Acme\x1b]0;pwn\x07",
        company__domain="acme.com",
    )
    lead_id = only(composed.engine, [hostile])
    runner = CliRunner()
    for args in (["leads", "show", str(lead_id)], ["leads", "list"]):
        result = runner.invoke(cli.app, args)
        assert result.exit_code == 0, result.output
        assert "\x1b" not in result.stdout
        assert "\x07" not in result.stdout
        assert "\r" not in result.stdout
    shown = runner.invoke(cli.app, ["leads", "show", str(lead_id)]).stdout
    assert "Ada\\x1b[2J Lovelace" in shown


# Verifies: specs/lead-source-adapters/requirements.md#22.1
def test_leads_show_an_unknown_or_malformed_id_fails_cleanly(
    composed: Backend, restore_structlog: None
) -> None:
    runner = CliRunner()
    unknown = runner.invoke(cli.app, ["leads", "show", str(uuid.uuid4())])
    malformed = runner.invoke(cli.app, ["leads", "show", "not-a-uuid"])
    assert unknown.exit_code == 1
    assert "no lead" in unknown.stderr
    assert malformed.exit_code == 2


def test_leads_commands_on_a_store_ingest_never_set_up_name_the_fix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, restore_structlog: None
) -> None:
    """A fresh database has no tables; the readers say so instead of a SQL trace."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LEADFORGE_ENV_FILE", raising=False)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'empty.db'}")
    runner = CliRunner()
    for args in (["leads", "list"], ["leads", "show", str(uuid.uuid4())]):
        result = runner.invoke(cli.app, args)
        assert result.exit_code == cli.EXIT_CONFIGURATION_ERROR, result.output
        assert "no schema" in result.stderr
        assert "leadforge ingest" in result.stderr
        assert result.exception is None or isinstance(result.exception, SystemExit)


# Verifies: specs/lead-source-adapters/requirements.md#22.1
def test_leads_show_names_the_successor_of_a_retired_lead(
    composed: Backend, restore_structlog: None
) -> None:
    absorbed, survivor = _joined(composed.engine)
    result = CliRunner().invoke(cli.app, ["leads", "show", str(absorbed)])
    assert result.exit_code == 0, result.output
    assert f"retired -> {survivor}" in result.stdout


# Verifies: specs/lead-source-adapters/requirements.md#22.1
def test_leads_list_prints_one_masked_line_per_lead_and_a_cursor(
    composed: Backend, restore_structlog: None
) -> None:
    ids = sorted(_people(composed.engine, 3), key=str)
    runner = CliRunner()
    first = runner.invoke(cli.app, ["leads", "list", "--limit", "2"])
    assert first.exit_code == 0, first.output
    lines = first.stdout.splitlines()
    assert [ln.split()[0] for ln in lines[:2]] == [str(i) for i in ids[:2]]
    assert lines[2] == f"next: --after {ids[1]}"
    assert "p***@acme.com" in first.stdout
    assert "p0@" not in first.stdout
    rest = runner.invoke(cli.app, ["leads", "list", "--after", str(ids[1])])
    assert rest.exit_code == 0, rest.output
    assert [ln.split()[0] for ln in rest.stdout.splitlines()] == [str(ids[2])]
    revealed = runner.invoke(cli.app, ["leads", "list", "--reveal"])
    assert "p0@acme.com" in revealed.stdout or "p1@acme.com" in revealed.stdout
