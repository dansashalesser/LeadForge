"""The report shows the verdict and the evidence behind it (user-recognition 8.4)."""

import uuid
from datetime import date

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.store import models as m
from leadforge.outreach.decisions import (
    Decision,
    EvidenceRef,
    Reason,
    record_decisions,
)
from leadforge.outreach.report import build_report, render_json, render_markdown
from leadforge.outreach.searches import start_search
from leadforge.outreach.tests.support import (  # noqa: F401
    NOW,
    Backend,
    backend,
    blank,
    make_plan,
    postgres_url,
)


@pytest.fixture
def engine(backend: Backend) -> sa.Engine:  # noqa: F811
    return backend.engine


URL = "https://acme.example/blog/migration?a=1&b=2"
QUOTE = "We run <b>astra</b> in production"


def _seed(engine: sa.Engine, mode: str = "users") -> uuid.UUID:
    refs = (
        EvidenceRef(
            evidence_class="own_domain_content",
            source="serp",
            url=URL,
            observed_on=date(2026, 1, 2),
            quote=QUOTE,
            relationship="uses_now",
        ),
        EvidenceRef(
            evidence_class="person_self_stated",
            source="profile",
            url="javascript:alert(1)",
            quote="I built our astra cluster",
            relationship="uses_now",
        ),
    )
    with Session(engine) as session, session.begin():
        search = start_search(session, make_plan(mode=mode), now=NOW)
        lead = uuid.uuid4()
        session.add(m.LeadIdentity(id=lead, created_at=NOW))
        session.flush()
        record_decisions(
            session,
            search,
            (
                Decision(
                    lead_id=lead,
                    status="selected",
                    score=1,
                    reasons=(
                        Reason(code="verified_core", evidence_refs=refs),
                        Reason(code="icp_fit", value=1, weight=1),
                    ),
                ),
            ),
            now=NOW,
        )
    return search


# Verifies: specs/user-recognition/requirements.md#8.4
def test_a_users_lead_row_carries_verdict_and_sectioned_evidence(
    engine: sa.Engine,
) -> None:
    search = _seed(engine)
    with Session(engine) as session:
        row = build_report(session, search).leads[0]

    assert row.verdict == "verified_core"
    company, person = row.evidence
    assert (company.section, person.section) == ("company_usage", "person_fit")
    assert company.url == URL
    assert company.quote == QUOTE
    assert company.observed_on == date(2026, 1, 2)
    assert company.relationship == "uses_now"
    assert company.evidence_class == "own_domain_content"


# Verifies: specs/user-recognition/requirements.md#8.4
def test_other_modes_have_no_verdict(engine: sa.Engine) -> None:
    search = _seed(engine, mode="free_text")
    with Session(engine) as session:
        row = build_report(session, search).leads[0]

    assert row.verdict is None


# Verifies: specs/user-recognition/requirements.md#8.4
def test_markdown_shows_verdict_sections_and_quotes_linked_to_urls(
    engine: sa.Engine,
) -> None:
    search = _seed(engine)
    with Session(engine) as session:
        report = build_report(session, search)
    text = render_markdown(report)

    assert "- verdict: verified_core" in text
    assert "Company Usage" in text
    assert "Person Fit" in text
    assert f"> {QUOTE}" in text
    assert "<https://acme.example/blog/migration?a=1&b=2>" in text
    assert "javascript:" not in text
    assert '"url"' in render_json(report)
