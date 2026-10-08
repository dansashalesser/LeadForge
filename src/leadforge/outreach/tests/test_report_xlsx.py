"""The report workbook: selected people first, and page text never a formula."""

import io
import uuid
from decimal import Decimal

from openpyxl import load_workbook

from leadforge.outreach.report import (
    EvidenceItem,
    Funnel,
    LeadRow,
    Report,
    render_xlsx,
)


def _row(status: str, score: str, quote: str = "uses the product") -> LeadRow:
    return LeadRow(
        lead_id=uuid.uuid4(),
        status=status,
        score=Decimal(score),
        reasons=("usage_confirmed", "seniority=0.8"),
        evidence=(
            EvidenceItem(
                section="company_usage",
                evidence_class="job_posting",
                source="serp",
                url="https://example.com/job",
                observed_on=None,
                quote=quote,
                relationship="employer",
            ),
        ),
        sequence="not started",
        name=None,
        email="***@acme.example",
        linkedin_url=None,
        invite=None,
        email_subject=None,
        email_body=None,
    )


def test_selected_rank_first_by_score_and_quotes_stay_text() -> None:
    rejected = _row("rejected", "0.9", quote='=HYPERLINK("https://evil.example")')
    low, high = _row("selected", "0.4"), _row("selected", "0.7")
    report = Report(
        search_id=uuid.uuid4(),
        mode="users",
        query="vendor:acme",
        notes=("compiler: offline",),
        funnel=Funnel(
            gathered=3,
            selected=2,
            invited=0,
            emailed=0,
            stalled=0,
            rejected=1,
            needs_enrichment=0,
            manual_review=0,
        ),
        leads=(rejected, low, high),
        revealed=False,
    )

    book = load_workbook(io.BytesIO(render_xlsx(report)))

    leads = list(book["Leads"].values)
    order = [high.lead_id, low.lead_id, rejected.lead_id]
    assert [r[0] for r in leads[1:]] == [str(i) for i in order]
    assert leads[1][10] == "usage_confirmed, seniority=0.8"
    evidence = book["Evidence"]
    quotes = [c for c in evidence["H"][1:] if str(c.value).startswith("=")]
    assert quotes
    assert all(c.data_type == "s" for c in quotes)
    assert ("Query", "vendor:acme") in list(book["Summary"].values)
